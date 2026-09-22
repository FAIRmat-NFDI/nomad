#
# Copyright The NOMAD Authors.
#
# This file is part of NOMAD. See https://nomad-lab.eu for further info.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import base64
from collections.abc import Iterable

from cryptography.fernet import Fernet
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from nomad.auth.tokens import check_api_secret
from nomad.config import config

_ENCRYPTED_ENCODING = b'binary/encrypted'
_KEY_ID_METADATA = 'encryption-key-id'


def _create_fernet(secret: str) -> Fernet:
    # Fernet requires a URL-safe, base64-encoded 32-byte key. Pad shorter
    # secrets (as used in tests) and truncate longer ones.
    secret_bytes = secret.encode()
    padded_key = b'\0' * max(32 - len(secret_bytes), 0) + secret_bytes
    return Fernet(base64.urlsafe_b64encode(padded_key[:32]))


class EncryptionCodec(PayloadCodec):
    """A PayloadCodec that encrypts/decrypts all Payloads."""

    def __init__(self) -> None:
        super().__init__()

        # Payloads using the default key retain the existing metadata format so
        # previously recorded workflow histories remain decryptable during replay.
        check_api_secret()
        self.default_fernet = _create_fernet(
            config.services.api_secret.get_secret_value()
        )

        codec_key = config.temporal.payload_codec_key
        self.key_id = config.temporal.payload_codec_key_id if codec_key else None
        self.fernet = (
            _create_fernet(codec_key.get_secret_value())
            if codec_key
            else self.default_fernet
        )

    async def encode(self, payloads: Iterable[Payload]) -> list[Payload]:
        """Encrypt all payloads during encoding."""
        encoded_payloads = []
        for payload in payloads:
            metadata = {'encoding': _ENCRYPTED_ENCODING}
            if self.key_id is not None:
                metadata[_KEY_ID_METADATA] = self.key_id.encode()
            encoded_payloads.append(
                Payload(
                    metadata=metadata,
                    data=self.encrypt(payload.SerializeToString()),
                )
            )
        return encoded_payloads

    async def decode(self, payloads: Iterable[Payload]) -> list[Payload]:
        """Decode all payloads decrypting those with expected encoding."""
        ret: list[Payload] = []
        for p in payloads:
            # Ignore ones without our expected encoding
            if p.metadata.get('encoding') != _ENCRYPTED_ENCODING:
                ret.append(p)
                continue

            payload_key_id = p.metadata.get(_KEY_ID_METADATA)
            if payload_key_id is None:
                fernet = self.default_fernet
            else:
                key_id = payload_key_id.decode()
                if key_id != self.key_id:
                    raise ValueError(
                        f'No Temporal payload codec key configured for key ID {key_id!r}'
                    )
                fernet = self.fernet

            ret.append(Payload.FromString(fernet.decrypt(p.data)))
        return ret

    def encrypt(self, data: bytes) -> bytes:
        """Return data encrypted."""
        return self.fernet.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        """Return data decrypted."""
        return self.fernet.decrypt(data)
