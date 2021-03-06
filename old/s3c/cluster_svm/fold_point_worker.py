#TODO: support concatenating multiple datasets
print 'script launched'

try:
    from ia3n.util.mem import MemoryMonitor
    mem = MemoryMonitor()
except ImportError:
    mem = None
if mem:
    print 'memory usage on launch: '+str(mem.usage())
import numpy as np
import warnings
from optparse import OptionParser
try:
    from sklearn.svm import LinearSVC, SVC
except ImportError:
    from scikits.learn.svm import LinearSVC, SVC
from galatea.s3c.feature_loading import get_features
from pylearn2.utils import serial
from pylearn2.datasets.cifar10 import CIFAR10
from pylearn2.datasets.cifar100 import CIFAR100
try:
    from pylearn2.models.svm import DenseMulticlassSVM
except:
    DenseMulticlassSVM = None
import gc
gc.collect()
if mem:
    print 'memory usage after imports'+str(mem.usage())

def get_svm_type(C, one_against_many):
    #using standard SVM rather than Adam's L2 SVM
    if one_against_many:
        svm_type = DenseMulticlassSVM(C=C)
    else:
        svm_type =  SVC(kernel='linear',C=C)
    return svm_type


def subtrain(fold_train_X, fold_train_y, C, one_against_many):
    assert str(fold_train_X.dtype) == 'float64'

    if mem:
        print 'mem usage before calling fit: '+str(mem.usage())
    svm = get_svm_type(C, one_against_many).fit(fold_train_X, fold_train_y)
    gc.collect()
    if mem:
        print 'mem usage after calling fit: '+str(mem.usage())
    return svm

def validate(train_X, train_y, fold_indices, C, log, one_against_many):
    train_mask = np.zeros((train_X.shape[0],),dtype='uint8')
    #Yes, Adam's site really does say to use the 1000 indices as the train,
    #not the validation set
    #The -1 is to convert from matlab indices
    train_mask[fold_indices-1] = 1

    if mem:
        print 'mem usage before calling subtrain: '+str(mem.usage())
    log.write('training...\n')
    log.flush()
    sub_train_X = np.cast['float64'](train_X[train_mask.astype(bool),:])
    svm = subtrain( sub_train_X, train_y[train_mask.astype(bool)], \
            C = C, one_against_many = one_against_many)
    gc.collect()
    if mem:
        print 'mem usage after calling subtrain: '+str(mem.usage())


    log.write('predicting...\n')
    log.flush()
    this_fold_valid_X = train_X[(1-train_mask).astype(bool),:]
    y_pred = svm.predict(this_fold_valid_X)
    this_fold_valid_y = train_y[(1-train_mask).astype(bool)]

    rval = (this_fold_valid_y == y_pred).mean()

    return rval


def get_labels_and_fold_indices(cifar10, cifar100, stl10):
    assert stl10 or cifar10 or cifar100
    assert stl10+cifar10+cifar100 == 1

    if stl10:
        print 'loading entire stl-10 train set just to get the labels and folds'
        stl10 = serial.load("${PYLEARN2_DATA_PATH}/stl10/stl10_32x32/train.pkl")
        train_y = stl10.y

        fold_indices = stl10.fold_indices
    elif cifar10 or cifar100:
        if cifar10:
            print 'loading entire cifar10 train set just to get the labels'
            cifar = CIFAR10(which_set = 'train')
        else:
            assert cifar100
            print 'loading entire cifar100 train set just to get the labels'
            cifar = CIFAR100(which_set = 'train')
            cifar.y = cifar.y_fine
        train_y = cifar.y
        assert train_y is not None

        fold_indices = np.zeros((5,40000),dtype='uint16')
        idx_list = np.cast['uint16'](np.arange(1,50001)) #mimic matlab format of stl10
        for i in xrange(5):
            mask = idx_list < i * 10000 + 1
            mask += idx_list >= (i+1) * 10000 + 1
            fold_indices[i,:] = idx_list[mask]
        assert fold_indices.min() == 1
        assert fold_indices.max() == 50000


    return train_y, fold_indices


def main(train_path,
        out_path,
        dataset,
        standardize,
        fold,
        C,
        log,
        **kwargs):

    log.write('in main\n')
    log.flush()


    stl10 = dataset == 'stl10'
    cifar10 = dataset == 'cifar10'
    cifar100 = dataset == 'cifar100'
    assert stl10 + cifar10 + cifar100 == 1

    print 'getting labels and oflds'
    if mem:
        print 'mem usage before getting labels and folds '+str(mem.usage())
    train_y, fold_indices = get_labels_and_fold_indices(cifar10, cifar100, stl10)
    if mem:
        print 'mem usage after getting labels and folds '+str(mem.usage())
    gc.collect()
    assert train_y is not None
    log.write('got labels and folds')
    log.flush()

    print 'loading training features'
    train_X = get_features(train_path, split = False, standardize = standardize)
    log.write('got features')
    log.flush()


    assert str(train_X.dtype) == 'float32'
    if stl10:
        assert train_X.shape[0] == 5000
    if cifar10 or cifar100:
        assert train_X.shape[0] == 50000
        assert train_y.shape == (50000,)

    print 'running validate'
    acc = validate(train_X, train_y, fold_indices[fold,:], C, log, **kwargs)

    report = open(out_path, 'w')
    report.write('C\tfold\tvalidation accuracy\n%f\t%d\t%f\n' % (C, fold, acc))
    report.close()

if __name__ == '__main__':
    print '__main__ detected'

    parser = OptionParser()
    parser.add_option("-d", "--train",
                action="store", type="string", dest="train")
    parser.add_option("-o", "--out",
                action="store", type="string", dest="out")
    parser.add_option("--one-against-one", action="store_false", dest="one_against_many", default=True,
                      help="use a one-against-one classifier rather than a one-against-many classifier")
    parser.add_option('-C', type='float', dest='C', action='store', default = None)
    parser.add_option('--dataset', type='string', dest = 'dataset', action='store', default = None)
    parser.add_option('--standardize',action="store_true", dest="standardize", default=False)
    parser.add_option('--fold', action='store', type='int', dest='fold', default = None)

    (options, args) = parser.parse_args()

    assert options.dataset
    assert options.C
    assert options.out


    log = open(options.out+'.log.txt','w')
    log.write('log file started succesfully\n')
    log.flush()

    print 'parsed the args'
    main(train_path=options.train,
         out_path = options.out,
         one_against_many = options.one_against_many,
         C = options.C,
         dataset = options.dataset,
         standardize = options.standardize,
         fold = options.fold,
         log = log
    )

    log.close()
