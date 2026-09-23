import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# def encrypt_api_key(api_key: str, encryption_key: str) -> str:
#     key = base64.b64decode(encryption_key)
#     aesgcm = AESGCM(key)

#     nonce = os.urandom(12)
#     encrypted = aesgcm.encrypt(nonce, api_key.encode("utf-8"), None)

#     return base64.b64encode(nonce + encrypted).decode("utf-8")

# api_key = input('enter api_key: ')
# encryption_key = input('enter encryption_key: ')
# print(encrypt_api_key(api_key, encryption_key))

def decrypt_api_key(encrypted_api_key: str, encryption_key: str) -> str:
    key = base64.b64decode(encryption_key)
    encrypted_data = base64.b64decode(encrypted_api_key)

    nonce = encrypted_data[:12]
    ciphertext_and_tag = encrypted_data[12:]

    aesgcm = AESGCM(key)

    decrypted = aesgcm.decrypt(
        nonce,
        ciphertext_and_tag,
        None
    )

    return decrypted.decode("utf-8")

api_key = input('enter enc_api_key: ')
encryption_key = input('enter encryption_key: ')
print(decrypt_api_key(api_key, encryption_key))
 