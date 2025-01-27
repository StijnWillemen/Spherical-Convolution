#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import h5py
import numpy as np

SphConv_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(SphConv_ROOT)

LOG_ROOT = os.path.join(SphConv_ROOT, "Log/crop_srcs")
if not os.path.isdir(LOG_ROOT):
    os.makedirs(LOG_ROOT)

from cfg import DATA_ROOT
from SphereProjection import crop_image
from util.rf import top_down, rounded_rf, strides
from util.data_io import load_pkl, get_frameId

def crop_srcs(layer, process, log, **kwargs):
    sphereH = kwargs.get("sphereH", 320)
    ks = kwargs.get("ks", 640)
    network = kwargs.get("network", "faster-rcnn")
    n_process = kwargs.get("n_process", 4)
    split = kwargs.get("split", "train")
    stride = strides[ks][sphereH][layer]

    # Check source directory
    bot = top_down[layer]
    src_dir = os.path.join(DATA_ROOT,
                           "SourceSphereH{0}Ks{1}".format(sphereH, ks),
                           "{0}{1}".format(network, bot))
    if not os.path.isdir(src_dir):
        raise ValueError("{} does not exist.".format(src_dir))

    # Load target
    target_dir = "TargetSphereH{0}Ks{1}/{2}{3}".format(sphereH, ks, network, layer)
    targets = {}
    for tilt in range(process, sphereH, n_process):
        prefix = os.path.join(DATA_ROOT, target_dir, "tilt{0:03d}.{1}".format(tilt, split))
        dst_path = "{}.h5".format(prefix)
        if os.path.isfile(dst_path):
            log.write("{} exists.\n".format(dst_path))
            continue
        lock_path = "{}.lock".format(dst_path)
        if os.path.isfile(lock_path):
            log.write("{} is being generated by other process.\n".format(lock_path))
            continue
        open(lock_path, "w").close()
        target_path = "{}.pkl".format(prefix)
        target = load_pkl(target_path)
        targets[tilt] = target
    if len(targets) == 0:
        log.write("No target to generate.\n")
        return

    # Reorder target into path -> tilt -> (x, y, target)
    reordered = {}
    outputs = {}
    for tilt, data in targets.iteritems():
        outputs[tilt] = {"x": [], "y": [], "target": [], "srcs": [], "path": []}
        for path, x, y, target in data:
            assert tilt == y
            frameId = get_frameId(path)
            if frameId in reordered:
                img_data = reordered[frameId]
            else:
                img_data = {}
                img_data[tilt] = []
                reordered[frameId] = img_data
            if tilt in img_data:
                tilt_data = img_data[tilt]
            else:
                tilt_data = []
                img_data[tilt] = tilt_data
            sample = (x, y, np.array(target))
            tilt_data.append(sample)
    targets = None
    total = sum(len(img_data[tilt]) for img_data in reordered.itervalues())
    width = len(str(total))
    log.write("Total {} samples.\n".format(total))

    for frameId, img_data in reordered.iteritems():
        src_path = os.path.join(src_dir, '{}.h5'.format(frameId))
        if not os.path.isfile(src_path):
            raise ValueError("{} does not exist.".format(src_path))
        #log.write("Read input {}\n".format(src_path))
        #log.flush()
        with h5py.File(src_path, 'r') as hf:
            frame = hf[frameId][:].astype(np.float32)
            for tilt, tilt_data in img_data.iteritems():
                crop_size, crop_out = rounded_rf(layer, tilt, sphereH=sphereH)
                for x, y, target in tilt_data:
                    src = crop_image(frame, x, y, crop_size)
                    if stride > 1:
                        src = src[::stride,::stride,:]
                    assert src.shape[1] == crop_out[0]
                    assert src.shape[0] == crop_out[1]
                    outputs[tilt]["x"].append(x)
                    outputs[tilt]["y"].append(y)
                    outputs[tilt]["target"].append(target)
                    outputs[tilt]["path"].append(frameId)
                    outputs[tilt]["srcs"].append(src)
        n_samples = len(outputs[tilt]["x"])
        if n_samples % 1000 == 0:
            log.write("Load {0:{width}d}/{1} samples\n".format(n_samples, total, width=width))
            log.flush()

    # Write to .h5
    log.write("Read data finished\n")
    for tilt in outputs:
        prefix = os.path.join(DATA_ROOT, target_dir, "tilt{0:03d}.{1}".format(tilt, split))
        dst_path = "{}.h5".format(prefix)
        if os.path.isfile(dst_path):
            continue
        xs = outputs[tilt]["x"]
        xs = np.array(xs)
        ys = outputs[tilt]["y"]
        ys = np.array(ys)
        target = outputs[tilt]["target"]
        target = np.vstack(target)
        srcs = outputs[tilt]["srcs"]
        srcs = np.stack(srcs)
        paths = outputs[tilt]["path"]

        with h5py.File(dst_path, 'w') as hf:
            hf.create_dataset("path", data=paths)
            hf.create_dataset("x", data=xs)
            hf.create_dataset("y", data=ys)
            hf.create_dataset("target", data=target, compression="lzf", shuffle=True)
            hf.create_dataset("srcs", data=srcs, compression="lzf", shuffle=True)
        log.write("Write {} finished\n".format(dst_path))
        lock_path = "{}.lock".format(dst_path)
        try:
            os.remove(lock_path)
        except:
            continue
    sys.stdout.write("Process {0}/{1} ({2}) finish.\n".format(process, n_process, split))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--network', dest='network',
                        choices=['vgg16', 'faster-rcnn'], default='faster-rcnn', type=str)
    parser.add_argument('--sphereH', dest='sphereH', type=int, default=320)
    parser.add_argument('--ks', dest='ks', type=int, default=640)
    parser.add_argument('--n_process', dest='n_process', type=int, default=16)
    parser.add_argument('layer', choices=top_down.keys(), type=str)
    parser.add_argument('process', type=int)
    args = parser.parse_args()

    log_dir = os.path.join(LOG_ROOT, "SphereH{0}".format(args.sphereH),
                           "{0}{1}".format(args.network, args.layer))
    if not os.path.isdir(log_dir):
        os.makedirs(log_dir)
    log_path = os.path.join(log_dir, "process{0:02d}.log".format(args.process))
    log = open(log_path, 'w')

    kwargs = {
        "sphereH": args.sphereH,
        "ks": args.ks,
        "network": args.network,
        "n_process": args.n_process,
    }
    kwargs["split"] = "test"
    crop_srcs(args.layer, args.process, log, **kwargs)
    kwargs["split"] = "train"
    crop_srcs(args.layer, args.process, log, **kwargs)
    log.close()

if __name__ == "__main__":
    main()
