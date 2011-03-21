"""
Several utilities for experimenting upon utlc datasets
"""
# Standard library imports
import os
from itertools import repeat

# Third-party imports
import numpy
import theano
from theano import tensor
from pylearn.datasets.utlc import load_ndarray_dataset

# Local imports
from auc import embed

##################################################
# Shortcuts and auxiliary functions
##################################################

floatX = theano.config.floatX

def get_constant(variable):
    """ Little hack to return the python value of a theano shared variable """
    return theano.function([],
                           variable,
                           mode=theano.compile.Mode(linker='py')
                           )()

def sharedX(value, name=None, borrow=False):
    """Transform value into a shared variable of type floatX"""
    return theano.shared(theano._asarray(value, dtype=floatX),
                         name=name,
                         borrow=borrow)

def subdict(d, keys):
    """ Create a subdictionary of d with the keys in keys """
    result = {}
    for key in keys:
        if key in d: result[key] = d[key]
    return result

def safe_update(dict_to, dict_from):
    """
    Like dict_to.update(dict_from), except don't overwrite any keys.
    """
    for key, val in dict(dict_from).iteritems():
        if key in dict_to:
            raise KeyError(key)
        dict_to[key] = val
    return dict_to

def getboth(dict1, dict2, key, default=None):
    """
    Try to retrieve key from dict1 if exists, otherwise try with dict2.
    If the key is not found in any of them, raise an exception.
    """
    try:
        return dict1[key]
    except KeyError:
        if default is None:
            return dict2[key]
        else:
            return dict2.get(key, default)

##################################################
# Datasets and contest facilities
##################################################

def load_data(conf):
    """
    Loads a specified dataset according to the parameters in the dictionary
    """
    expected = ['normalize',
                'normalize_on_the_fly',
                'randomize_valid',
                'randomize_test',
                'transfer']
    print '... loading Dataset'
    data = load_ndarray_dataset(conf['dataset'], **subdict(conf, expected))

    # Allocate shared variables
    def shared_dataset(data_x):
        """Function that loads the dataset into shared variables"""
        if conf.get('normalize', True):
            return sharedX(data_x, borrow=True)
        else:
            return theano.shared(theano._asarray(data_x), borrow=True)

    if conf.get('normalize_on_the_fly', False):
        return data
    else:
        return map(shared_dataset, data)

def create_submission(conf, transform_valid, transform_test = None):
    """
    Create submission files given a configuration dictionary and a
    computation function.

    Note that it always reload the datasets to ensure valid & test
    are not permuted.
    """
    if transform_test is None:
        transform_test = transform_valid
    
    # Load the dataset, without permuting valid and test
    kwargs = subdict(conf, ['dataset', 'normalize', 'normalize_on_the_fly'])
    kwargs.update(randomize_valid=False, randomize_test=False)
    valid_set, test_set = load_data(kwargs)[1:3]

    # Valid and test representations
    valid_repr = transform_valid(valid_set.value)
    test_repr = transform_test(test_set.value)

    # If there are too much features, outputs kernel matrices
    if (valid_repr.shape[1] > valid_repr.shape[0]):
        valid_repr = numpy.dot(valid_repr, valid_repr.T)
        test_repr = numpy.dot(test_repr, test_repr.T)

    # Quantitize data
    valid_repr = numpy.floor((valid_repr / valid_repr.max())*999)
    test_repr = numpy.floor((test_repr / test_repr.max())*999)

    # Convert into text info
    valid_text = ''
    test_text = ''

    for i in xrange(valid_repr.shape[0]):
        for j in xrange(valid_repr.shape[1]):
            valid_text += '%s ' % int(valid_repr[i, j])
        valid_text += '\n'
    del valid_repr

    for i in xrange(test_repr.shape[0]):
        for j in xrange(test_repr.shape[1]):
            test_text += '%s ' % int(test_repr[i, j])
        test_text += '\n'
    del test_repr

    # Write it in a .txt file
    submit_dir = conf['savedir']
    if not os.path.exists(submit_dir):
        os.mkdir(submit_dir)
    elif not os.path.isdir(submit_dir):
        raise IOError('savedir %s is not a directory' % submit_dir)

    basename = os.path.join(submit_dir,
                            conf['dataset'] + '_' + conf['expname']
                            )
    valid_file = open(basename + '_valid.prepro', 'w')
    test_file = open(basename + '_final.prepro', 'w')

    valid_file.write(valid_text)
    test_file.write(test_text)

    valid_file.close()
    test_file.close()

    print "... done creating files"

    os.system('zip -j %s %s %s' % (basename + '.zip',
                                   basename + '_valid.prepro',
                                   basename + '_final.prepro'))

    print "... files compressed"

    os.system('rm %s %s' % (basename + '_valid.prepro',
                            basename + '_final.prepro'))

    print "... useless files deleted"

##################################################
# Proxies for representation evaluations
##################################################

def compute_alc(valid_repr, test_repr):
    """
    Returns the ALC of the valid set VS test set
    Note: This proxy won't work in the case of transductive learning
    (This is an assumption) but it seems to be a good proxy in the
    normal case (i.e only train on training set)
    """

    # Concatenate the sets, and give different one hot labels for valid and test
    n_valid = valid_repr.shape[0]
    n_test = test_repr.shape[0]

    _labvalid = numpy.hstack((numpy.ones((n_valid, 1)),
                              numpy.zeros((n_valid, 1))))
    _labtest = numpy.hstack((numpy.zeros((n_test, 1)),
                             numpy.ones((n_test, 1))))

    dataset = numpy.vstack((valid_repr, test_repr))
    label = numpy.vstack((_labvalid, _labtest))

    print '... computing the ALC'
    return embed.score(dataset, label)


def lookup_alc(data, transform):
    valid_repr = transform(data[1].value)
    test_repr = transform(data[2].value)

    return compute_alc(valid_repr, test_repr)


def filter_labels(train, label):
    """ Filter examples of train for which we have labels """
    # Examples for which any label is set
    condition = label.value.any(axis=1)

    # Compress train and label arrays according to condition
    def aux(var):
        return var.value.compress(condition, axis=0)

    return (aux(train), aux(label))


##################################################
# Iterator object for blending datasets
##################################################

class BatchIterator(object):
    """
    Builds an iterator object that can be used to go through the minibatches
    of a dataset, with respect to the given proportions in conf
    """
    def __init__(self, dataset, set_proba, batch_size, seed = 300):
        # Local shortcuts for array operations
        flo = numpy.floor
        sub = numpy.subtract
        mul = numpy.multiply
        div = numpy.divide
        mod = numpy.mod

        # Record external parameters
        self.batch_size = batch_size
        self.dataset = dataset

        # Compute maximum number of samples for one loop
        set_sizes = [get_constant(data.shape[0]) for data in dataset]
        set_batch = [float(self.batch_size) for i in xrange(3)]
        set_range = div(mul(set_proba, set_sizes), set_batch)
        set_range = map(int, numpy.ceil(set_range))

        # Upper bounds for each minibatch indexes
        set_limit = numpy.ceil(numpy.divide(set_sizes, set_batch))
        self.limit = map(int, set_limit)

        # Number of rows in the resulting union
        set_tsign = sub(set_limit, flo(div(set_sizes, set_batch)))
        set_tsize = mul(set_tsign, flo(div(set_range, set_limit)))

        l_trun = mul(flo(div(set_range, set_limit)), mod(set_sizes, set_batch))
        l_full = mul(sub(set_range, set_tsize), set_batch)

        self.length = sum(l_full) + sum(l_trun)

        # Random number generation using a permutation
        index_tab = []
        for i in xrange(3):
            index_tab.extend(repeat(i, set_range[i]))

        # Use a deterministic seed
        self.seed = seed
        rng = numpy.random.RandomState(seed=self.seed)
        self.permut = rng.permutation(index_tab)
                                      
    def __iter__(self):
        """ Generator function to iterate through all minibatches """
        counter = [0, 0, 0]
        for chosen in self.permut:
            # Retrieve minibatch from chosen set
            index = counter[chosen]
            minibatch = self.dataset[chosen].value[
                index * self.batch_size:(index + 1) * self.batch_size
            ]
            # Increment the related counter
            counter[chosen] = (counter[chosen] + 1) % self.limit[chosen]
            # Return the computed minibatch
            yield minibatch
    
    def __len__(self):
        """ Return length of the weighted union """
        return self.length

    def by_index(self):
        """ Same generator as __iter__, but yield only the chosen indexes """
        counter = [0, 0, 0]
        for chosen in self.permut:
            index = counter[chosen]
            counter[chosen] = (counter[chosen] + 1) % self.limit[chosen]
            yield chosen, index
    
    def by_subtensor(self):
        """ Generator function to iterate through all minibatches subtensors """
        counter = [0, 0, 0]
        for chosen in self.permut:
            # Retrieve minibatch from chosen set
            index = counter[chosen]
            minibatch = self.dataset[chosen][
                index * self.batch_size:(index + 1) * self.batch_size
            ]
            # Increment the related counter
            counter[chosen] = (counter[chosen] + 1) % self.limit[chosen]
            # Return the computed minibatch
            yield minibatch
        

##################################################
# Miscellaneous
##################################################

def blend(dataset, set_proba, **kwargs):
    """
    Randomized blending of datasets in data according to parameters in conf
    """
    iterator = BatchIterator(dataset, set_proba, 1, **kwargs)
    nrow = len(iterator)
    ncol = get_constant(dataset[0].shape[1])
    array = numpy.empty((nrow, ncol), dataset[0].dtype)
    row = 0
    for chosen, index in iterator.by_index():
        array[row] = dataset[chosen].value[index]
        row += 1

    return sharedX(array, borrow=True)

def is_pure_elemwise(graph, inputs):
    """
    Checks whether a graph is purely elementwise and containing only
    inputs from a given list.

    Parameters
    ----------
    graph : TensorVariable object
        Graph to perform checks against.
    inputs : list
        List of acceptable inputs to the graph.

    Returns
    -------
    elemwise_or_not : bool
        Returns `True` if a) everything in the graph is an Elemwise or
        a DimShuffle (DimShuffles are only acceptable to broadcast
        up constants), and b) all nodes without an owner appear in `inputs`
        or are constants. Returns `False` otherwise.
    """
    allowed_ops = tensor.basic.DimShuffle, tensor.basic.Elemwise
    owner = graph.owner
    op = graph.owner.op if graph.owner is not None else None
    # Ownerless stuff is fine if it's in inputs.
    if owner is None and graph in inputs:
        return True
    # Constants are okay.
    elif owner is None and isinstance(graph, tensor.basic.TensorConstant):
        return True
    # But if it's not a constant and has no owner, it's not.
    elif owner is None and graph not in inputs:
        return False
    # Anything but Elemwise and DimShuffle should be rejected.
    elif op is not None and not isinstance(op, allowed_ops):
        return False
    else:
        if isinstance(graph.owner.op, tensor.basic.DimShuffle):
            shuffled = graph.owner.inputs[0]
            if not isinstance(shuffled, tensor.basic.TensorConstant):
                return False
        for inp in graph.owner.inputs:
            if not is_pure_elemwise(inp, inputs):
                return False
        return True
