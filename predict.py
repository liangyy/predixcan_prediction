import os
import sys
import gc
import argparse
import sqlite3
import datetime
from collections import defaultdict

import numpy as np
import h5py_cache
from tqdm import tqdm

from bgen.bgen_dosage import BGENDosage


def check_out_file(out_file):
    try:
        test_fo = open(out_file, 'w')
        test_fo.close()
    except IOError:
        print("ERROR: Cannot open {} for writing. ".format(out_file) +
              "Make sure path to file exists.")
        sys.exit(1)


class WeightsDB:
    def __init__(self, beta_file):
        self.conn = sqlite3.connect(beta_file)

    def query(self, sql, args=None):
        c = self.conn.cursor()
        if args:
            for ret in c.execute(sql, args):
                yield ret
        else:
            for ret in c.execute(sql):
                yield ret


class UniqueRsid:
    def __init__(self, beta_file):
        self.db = WeightsDB(beta_file)

    def __call__(self):
        print("{} Getting unique rsids...".format(datetime.datetime.now()))
        res = [x[0] for x in self.db.query("SELECT distinct rsid FROM weights")]
        return res


class GetApplicationsOf:
    def __init__(self, beta_file, preload_weights=True):
        self.db = WeightsDB(beta_file)
        if preload_weights:
            print("{} Preloading weights...".format(datetime.datetime.now()))
            self.tuples = defaultdict(list)
            for tup in self.db.query("SELECT rsid, gene, weight, eff_allele FROM weights"):
                self.tuples[tup[0]].append(tup[1:])
        else:
            self.tuples = None

    def __call__(self, rsid):
        if self.tuples:
            for tup in self.tuples[rsid]:
                yield tup
        else:
            for tup in self.db.query("SELECT gene, weight, eff_allele FROM weights WHERE rsid=?", (rsid,)):
                yield tup


class TranscriptionMatrix:
    def __init__(self, beta_file, bgen_sample_file, output_binary_file, cache_size=int(50 * (1024 ** 2))):
        self.D = None
        self.beta_file = beta_file
        self.bgen_sample_file = bgen_sample_file
        self.cache_size = int(cache_size)

        if not any(output_binary_file.lower().endswith(hdf5_suffix) for hdf5_suffix in ('.h5', '.hdf5')):
            self.output_binary_file = output_binary_file + '.h5'
        else:
            self.output_binary_file = output_binary_file

        self.complements = {"A": "T", "C": "G", "G": "C", "T": "A"}

    def get_gene_list(self):
        return [tup[0] for tup in WeightsDB(self.beta_file).query("SELECT DISTINCT gene FROM weights ORDER BY gene")]

    def update(self, gene, weight, ref_allele, allele, dosage_row, max_gene_chunk_size, max_sample_chunk_size):
        if self.D is None:
            self.gene_list = self.get_gene_list()
            self.gene_index = {gene: k for (k, gene) in enumerate(self.gene_list)}

            self.n_genes = len(self.gene_list)
            self.n_samples = len(dosage_row)

            self.D_file = h5py_cache.File(self.output_binary_file, 'w', chunk_cache_mem_size=self.cache_size)
            n_genes_chunk = self.n_genes
            n_samples_chunk = self.n_samples
            if max_gene_chunk_size > 0:
                n_genes_chunk = np.min((self.n_genes, max_gene_chunk_size))
            if max_sample_chunk_size > 0:
                n_samples_chunk = np.min((self.n_samples, max_sample_chunk_size))
            self.D = self.D_file.create_dataset("pred_expr", shape=(self.n_genes, self.n_samples),
                                                chunks=(n_genes_chunk, n_samples_chunk),
                                                dtype=np.dtype('float32'), scaleoffset=4, compression='gzip')

        if gene in self.gene_index:  # assumes dosage coding 0 to 2
            # assumes non-ambiguous SNPs to resolve strand issues:
            if ref_allele == allele or self.complements[ref_allele] == allele:
                self.D[self.gene_index[gene], :] += dosage_row * weight
            else:
                self.D[self.gene_index[gene], :] += (2 - dosage_row) * weight  # Update all cases for that gene

    def get_samples(self):
        with open(self.bgen_sample_file, 'r') as samples:
            for line_idx, line in enumerate(samples):
                if line_idx <= 1:
                    continue
                line_split = line.split()
                yield [line_split[0], line_split[1]]

    def save(self):
        sample_generator = self.get_samples()

        self.D_samples = self.D_file.create_dataset("samples", (self.n_samples,), dtype='S25')
        for col in range(0, self.D.shape[1]):
            try:
                self.D_samples[col] = np.string_(next(sample_generator)[0])
            except StopIteration:
                print("ERROR: There are not enough rows in your sample file!")
                print(
                    "Make sure dosage files and sample files have the same number of individuals in the same order.")
                os.remove(self.output_binary_file)
                sys.exit(1)

        genes_dset = self.D_file.create_dataset("genes", (len(self.gene_list),), dtype='S30')
        for gene_idx, gene in enumerate(self.gene_list):
            genes_dset[gene_idx] = np.string_(str(gene))

        self.D_file.close()

        # check number of samples
        try:
            next(sample_generator)
        except StopIteration:
            print("{} Predicted expression file complete!".format(datetime.datetime.now()))
        else:
            print("ERROR: There are too many rows in your sample file!")
            print("Make sure dosage files and sample files have the same number of individuals in the ame order.")
            if os.path.isfile(self.output_binary_file):
                os.remove(self.output_binary_file)
            sys.exit(1)


def get_all_dosages_from_bgen(bgen_dir, bgen_prefix, rsids, args):
    if args.autosomes is True:
        if '{chr_num}' not in bgen_prefix:
            print("--bgens-prefix should have {chr_num} if --autosomes are used")
            sys.exit()
        candidate_prefix = tuple([ bgen_prefix.format(chr_num = j) for j in range(1, 23) ])
        bgen_files = [x for x in sorted(os.listdir(bgen_dir)) if x.startswith(candidate_prefix) and x.endswith(".bgen")]
    else:
        bgen_files = [x for x in sorted(os.listdir(bgen_dir)) if x.startswith(bgen_prefix) and x.endswith(".bgen")]

    for idx, chrfile in enumerate(bgen_files):
        print("{} Processing {}".format(datetime.datetime.now(), chrfile))

        if idx > 0:
            del bgen_dosage
            gc.collect()
        bgen_dosage = BGENDosage(os.path.join(bgen_dir, chrfile), bgen_bgi=os.path.join(args.bgens_bgi_dir, chrfile), sample_path=args.bgens_sample_file)

        for variant_info in bgen_dosage.items(n_rows_cached=args.bgens_n_cache, include_rsid=rsids):
            yield variant_info.rsid, variant_info.allele1, variant_info.dosages


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights-file', required=True, help="SQLite database with rsid weights.")
    parser.add_argument('--output-file', required=True, help="Predicted expression file from earlier run of PrediXcan")
    parser.add_argument('--bgens-dir', required=True, help="Path to a directory of BGEN files.")
    parser.add_argument('--bgens-bgi-dir', default=None, help="Path to a directory of BGEN BGI files (the filename should match the corresponding BGEN files).")
    parser.add_argument('--bgens-prefix', default='', help="Prefix of filenames of BGEN files.")
    parser.add_argument('--bgens-sample-file', required=True, help="BGEN sample file.")
    parser.add_argument('--bgens-n-cache', type=int, default=100, help="Number of variants to process at a time.")
    parser.add_argument('--bgens-writing-cache-size', type=int, default=50, help="BGEN reading cache size in MB.")
    parser.add_argument('--max-sample-chunk-size', type=int, default=-1, help="Maximum number of chunks on sample axis (column). Set to -1 if do not want to use chunk. Default: -1")
    parser.add_argument('--max-gene-chunk-size', type=int, default=10, help="Maximum number of chunks on gene axis (row). Set to -1 if do not want to use chunk. Default: 10")
    parser.add_argument('--no-progress-bar', action="store_true", help="Disable progress bar")
    parser.add_argument('--autosomes', action="store_true", help="Use all autosomes 1..22. If set true, --bgens-prefix should contain {chr_num}")

    args = parser.parse_args()
    
    if args.bgens_bgi_dir is None:
        args.bgens_bgi_dir = args.bgens_dir

    check_out_file(args.output_file)
    get_applications_of = GetApplicationsOf(args.weights_file, True)
    transcription_matrix = TranscriptionMatrix(args.weights_file, args.bgens_sample_file, args.output_file, cache_size=(args.bgens_writing_cache_size * (1024 ** 2)))

    unique_rsids = UniqueRsid(args.weights_file)()
    all_dosages = get_all_dosages_from_bgen(args.bgens_dir, args.bgens_prefix, unique_rsids, args)
    
    for rsid, allele, dosage_row in tqdm(all_dosages, total=len(unique_rsids), disable=args.no_progress_bar):
        for gene, weight, ref_allele in get_applications_of(rsid):
            transcription_matrix.update(gene, weight, ref_allele, allele, dosage_row, args.max_gene_chunk_size, args.max_sample_chunk_size)


    transcription_matrix.save()

