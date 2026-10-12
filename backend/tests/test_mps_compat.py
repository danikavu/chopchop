import pytest
import torch
import torch.nn.functional as F

from chopchop import mps_compat

pytestmark = pytest.mark.skipif(not torch.backends.mps.is_available(), reason="needs Apple MPS")


@pytest.mark.parametrize("stride,padding,dilation,groups", [(4, 0, 1, 1), (4, 2, 1, 1), (1, 3, 2, 1), (2, 1, 1, 4)])
def test_long_conv1d_matches_cpu(stride, padding, dilation, groups):
    torch.manual_seed(0)
    x = torch.randn(2, 8, 300_001)
    w = torch.randn(16, 8 // groups, 8)
    b = torch.randn(16)
    ref = F.conv1d(x, w, b, stride, padding, dilation, groups)
    with mps_compat.mps_long_conv():
        got = F.conv1d(x.to("mps"), w.to("mps"), b.to("mps"), stride, padding, dilation, groups).cpu()
    assert got.shape == ref.shape
    assert (got - ref).abs().max() < 1e-3


@pytest.mark.parametrize("stride,padding,output_padding,groups", [(4, 0, 0, 1), (4, 2, 0, 1), (4, 0, 3, 1), (2, 1, 1, 4)])
def test_long_conv_transpose1d_matches_cpu(stride, padding, output_padding, groups):
    torch.manual_seed(1)
    x = torch.randn(1, 16, 90_000)
    w = torch.randn(16, 8 // groups, 8)
    b = torch.randn(8)
    ref = F.conv_transpose1d(x, w, b, stride, padding, output_padding, groups)
    with mps_compat.mps_long_conv():
        got = F.conv_transpose1d(x.to("mps"), w.to("mps"), b.to("mps"), stride, padding, output_padding, groups).cpu()
    assert got.shape == ref.shape
    assert (got - ref).abs().max() < 1e-3


def test_patch_is_removed_afterwards():
    with mps_compat.mps_long_conv():
        assert F.conv1d is not mps_compat._orig_conv1d
    assert F.conv1d is mps_compat._orig_conv1d
    assert F.conv_transpose1d is mps_compat._orig_conv_transpose1d
