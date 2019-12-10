# PrediXcan - Gene expression prediction scripts

Scripts to predict gene expression given PrediXcan models and genotype data.

# Create conda environment

```
$ conda env create -f environment.yml
$ conda activate predixcan_prediction  # load the environment just built
$ wget http://www.well.ox.ac.uk/~gav/resources/rbgen_<version>.tgz . # install extra dependency of R package rbgen
$ R -e "install.packages( 'rbgen_<version>.tgz', repos = NULL )"
```

See extra documentation of `rbgen` and `bgen` [here](https://bitbucket.org/gavinband/bgen/wiki/browse/)

# Index BGEN

The script relies on `rbgen` which needs your BGEN files being indexed by `bgenix`. 
See details [here](https://bitbucket.org/gavinband/bgen/wiki/bgenix).
