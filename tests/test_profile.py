"""HostProfile 模型测试（v2.8）。"""

import pytest

from remote_cmd.core.profile import HostProfile
from remote_cmd.utils.exceptions import ValidationError


class TestHostProfile:
    def test_minimal_profile(self):
        p = HostProfile(name="aws")
        assert p.username is None
        assert p.port is None
        assert p.key_filename is None
        assert p.tags == []
        assert p.description == ""

    def test_full_profile(self):
        p = HostProfile(
            name="aws",
            username="ec2-user",
            port=2222,
            key_filename="~/.ssh/aws.pem",
            tags=["cloud", "prod"],
            description="AWS fleet",
        )
        assert p.name == "aws"
        assert p.port == 2222

    @pytest.mark.parametrize("bad", ["", "   ", None, 123])
    def test_invalid_name_raises(self, bad):
        with pytest.raises(ValidationError, match="name"):
            HostProfile(name=bad)

    @pytest.mark.parametrize("bad", [0, -1, 65536, "22", 22.5, True])
    def test_invalid_port_raises(self, bad):
        with pytest.raises(ValidationError, match="port"):
            HostProfile(name="p", port=bad)

    def test_none_tags_normalized(self):
        p = HostProfile(name="p", tags=None)
        assert p.tags == []

    def test_invalid_tags_raise(self):
        with pytest.raises(ValidationError, match="tags"):
            HostProfile(name="p", tags=["ok", 1])

    def test_roundtrip(self):
        p = HostProfile(
            name="aws",
            username="ec2-user",
            port=22,
            key_filename="/k.pem",
            tags=["a"],
            description="d",
        )
        assert HostProfile.from_dict(p.to_dict()) == p

    def test_from_dict_ignores_unknown_and_missing(self):
        p = HostProfile.from_dict({"name": "x", "unknown_field": 1})
        assert p.name == "x"
        assert p.username is None

    def test_to_dict_has_no_credential_fields(self):
        data = HostProfile(name="p", username="u").to_dict()
        assert "password" not in data
        assert set(data) == {"name", "username", "port", "key_filename", "tags", "description"}

    def test_repr_contains_fields(self):
        text = repr(HostProfile(name="p", username="u", tags=["t"]))
        assert "HostProfile(name='p'" in text
        assert "username='u'" in text
