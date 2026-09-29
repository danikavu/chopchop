"""Let Demucs run on Apple GPUs (PyTorch MPS).

PyTorch's MPS backend rejects 1-D (transposed) convolutions whose output is longer
than 65536 steps ("Output channels > 65536 not supported at the MPS device"). Demucs'
time-domain branch convolves whole ~7.8 s segments (~344k samples), so it fails.

While ``mps_long_conv()`` is active, long convolutions on MPS tensors are computed in
chunks instead, which is mathematically identical:
- conv1d: each chunk of outputs needs a slice of the input plus the kernel overlap;
- conv_transpose1d: each input chunk yields a block of output; blocks are summed where
  they overlap (overlap-add).
Everything else, and every call on other devices or of short length, is untouched.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

import torch
import torch.nn.functional as F

MAX_OUT = 32768  # comfortably under the 65536 limit
_lock = threading.Lock()
_orig_conv1d = F.conv1d
_orig_conv_transpose1d = F.conv_transpose1d


def _single(v):
    return v[0] if isinstance(v, (tuple, list)) else v


def _conv1d(input, weight, bias=None, stride=1, padding=0, dilation=1, groups=1):
    if input.device.type != "mps" or isinstance(padding, str) or input.dim() != 3:
        return _orig_conv1d(input, weight, bias, stride, padding, dilation, groups)
    stride, padding, dilation = _single(stride), _single(padding), _single(dilation)
    span = (weight.shape[-1] - 1) * dilation + 1  # receptive field of one output
    length = input.shape[-1] + 2 * padding
    n_out = (length - span) // stride + 1
    if n_out <= MAX_OUT:
        return _orig_conv1d(input, weight, bias, stride, padding, dilation, groups)
    x = F.pad(input, (padding, padding)) if padding else input
    parts = []
    for o in range(0, n_out, MAX_OUT):
        n = min(MAX_OUT, n_out - o)
        start = o * stride
        parts.append(_orig_conv1d(x[..., start:start + (n - 1) * stride + span], weight, bias,
                                  stride, 0, dilation, groups))
    return torch.cat(parts, dim=-1)


def _conv_transpose1d(input, weight, bias=None, stride=1, padding=0, output_padding=0, groups=1, dilation=1):
    if input.device.type != "mps" or input.dim() != 3:
        return _orig_conv_transpose1d(input, weight, bias, stride, padding, output_padding, groups, dilation)
    stride, padding = _single(stride), _single(padding)
    output_padding, dilation = _single(output_padding), _single(dilation)
    span = (weight.shape[-1] - 1) * dilation + 1  # output block per input step
    length_in = input.shape[-1]
    full = (length_in - 1) * stride + span  # before padding / output_padding
    if full <= MAX_OUT:
        return _orig_conv_transpose1d(input, weight, bias, stride, padding, output_padding, groups, dilation)
    out_ch = weight.shape[1] * groups
    out = input.new_zeros(input.shape[0], out_ch, full + output_padding)
    per = max(1, (MAX_OUT - span) // stride + 1)  # input steps whose block fits in MAX_OUT
    for i in range(0, length_in, per):
        n = min(per, length_in - i)
        block = _orig_conv_transpose1d(input[..., i:i + n], weight, None, stride, 0, 0, groups, dilation)
        out[..., i * stride:i * stride + block.shape[-1]] += block
    if bias is not None:
        out += bias.view(1, -1, 1)
    end = out.shape[-1] - padding
    return out[..., padding:end]


@contextmanager
def mps_long_conv():
    """Route long MPS convolutions through the chunked versions (process-wide while active)."""
    with _lock:
        F.conv1d, F.conv_transpose1d = _conv1d, _conv_transpose1d
        try:
            yield
        finally:
            F.conv1d, F.conv_transpose1d = _orig_conv1d, _orig_conv_transpose1d
