"""Tests for utility helpers."""

from pyvivosun.util import clamp_fan_level, clamp_light_level, is_sentinel, scale_value


class TestScaleValue:
    def test_default_divisor(self) -> None:
        assert scale_value(2500) == 25.0

    def test_custom_divisor(self) -> None:
        assert scale_value(500, 10) == 50.0

    def test_zero(self) -> None:
        assert scale_value(0) == 0.0

    def test_negative(self) -> None:
        assert scale_value(-6666) == -66.66


class TestIsSentinel:
    def test_default_sentinel(self) -> None:
        assert is_sentinel(-6666) is True

    def test_not_sentinel(self) -> None:
        assert is_sentinel(2500) is False

    def test_custom_sentinel(self) -> None:
        assert is_sentinel(-9999, sentinel=-9999) is True

    def test_zero(self) -> None:
        assert is_sentinel(0) is False


class TestClampLightLevel:
    def test_below_min(self) -> None:
        assert clamp_light_level(10) == 25

    def test_above_max(self) -> None:
        assert clamp_light_level(150) == 100

    def test_in_range(self) -> None:
        assert clamp_light_level(50) == 50

    def test_at_min(self) -> None:
        assert clamp_light_level(25) == 25

    def test_at_max(self) -> None:
        assert clamp_light_level(100) == 100


class TestClampFanLevel:
    def test_below_min(self) -> None:
        assert clamp_fan_level(-1) == 0

    def test_above_max(self) -> None:
        assert clamp_fan_level(15) == 10

    def test_in_range(self) -> None:
        assert clamp_fan_level(5) == 5
