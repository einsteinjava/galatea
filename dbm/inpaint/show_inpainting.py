import numpy as np
from pylearn2.utils import serial
import sys
from dbm_inpaint import DBM_Inpaint_Binary
from dbm_inpaint import MaskGen
import theano.tensor as T
from theano import function
from pylearn2.config import yaml_parse
from pylearn2.gui.patch_viewer import PatchViewer

ignore, model_path = sys.argv
model = serial.load(model_path)

try:
    mask_gen = model.mask_gen
    cost = model.cost
    cost.mask_gen = mask_gen
except:
    try:
        drop_prob = model.dbm_inpaint_drop_prob
        n_iter = model.dbm_inpaint_n_iter
        balance = model.dbm_inpaint_balance
    except:
        drop_prob = 0.5
        n_iter = 5
        balance = False

    mask_gen = MaskGen(drop_prob = drop_prob, balance = balance)
    cost = DBM_Inpaint_Binary(mask_gen = mask_gen, n_iter = n_iter)

X = T.matrix()

denoising = cost(model,X,return_locals=True)

drop_mask = denoising['drop_mask']
X_hat = denoising['X_hat']

f = function([X],[drop_mask,X_hat])

dataset = yaml_parse.load(model.dataset_yaml_src)

rows = 10
cols = 3
m = rows * cols

X = dataset.get_batch_design(100)

drop_mask, X_hat = f(X)

X, drop_mask, X_hat = [ dataset.get_topological_view(mat)
        for mat in [X, drop_mask, X_hat] ]
X = dataset.adjust_for_viewer(X)
X_hat = dataset.adjust_for_viewer(X_hat)

pv = PatchViewer( (rows, cols*3), (X.shape[1], X.shape[2]), is_color = True)

for i in xrange(m):
    #add original patch
    patch = X[i,:,:,:]
    if patch.shape[-1] != 3:
        patch = np.concatenate( (patch,patch,patch), axis=2)
    pv.add_patch(patch, rescale = False)

    #mark the masked areas as red
    mask_patch = drop_mask[i,:,:,0]
    red_channel = patch[:,:,0]
    green_channel = patch[:,:,1]
    blue_channel = patch[:,:,2]
    red_channel[mask_patch == 1] = 1.
    green_channel[mask_patch == 1] = -1.
    blue_channel[mask_patch == 1] = -1.
    patch[:,:,0] = red_channel
    patch[:,:,1] = green_channel
    patch[:,:,2] = blue_channel
    pv.add_patch(patch, rescale = False)

    #add filled-in patch
    patch = X_hat[i,:,:,:]
    if patch.shape[-1] != 3:
        patch = np.concatenate( (patch,patch,patch), axis=2)
    pv.add_patch(patch, rescale = False)

pv.show()
