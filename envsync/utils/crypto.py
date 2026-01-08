from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from envsync.utils.logger import get_logger

log = get_logger(__name__)

# 加密数据前缀标记，用于明确识别加密内容
ENCRYPTED_PREFIX = "enc:v1:"


class SecretManager:
    """
    管理敏感信息的加密与解密。
    使用机器特定的密钥（基于用户目录和主机名）+ PBKDF2 生成加密密钥。
    """

    def __init__(self, key_dir: Optional[Path] = None):
        self.key_dir = key_dir or (Path.home() / ".envsync")
        self.key_file = self.key_dir / ".secret_key"

    def _get_or_create_key(self) -> bytes:
        """获取或创建加密密钥"""
        if self.key_file.exists():
            return self.key_file.read_bytes()

        # 生成基于机器和用户的唯一 salt
        import socket
        machine_id = f"{socket.gethostname()}-{os.getuid() if hasattr(os, 'getuid') else 'windows'}"
        salt = machine_id.encode()

        # 使用 PBKDF2HMAC 派生密钥
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        # 使用用户主目录路径作为密码材料
        password = str(Path.home()).encode()
        key = base64.urlsafe_b64encode(kdf.derive(password))

        # 保存密钥
        self.key_dir.mkdir(parents=True, exist_ok=True)
        self.key_file.write_bytes(key)
        self.key_file.chmod(0o600)  # 仅当前用户可读
        log.info("已生成加密密钥: %s", self.key_file)
        return key

    def encrypt(self, plaintext: str) -> str:
        """加密明文，返回带前缀标记的密文"""
        if not plaintext:
            return ""
        key = self._get_or_create_key()
        f = Fernet(key)
        encrypted = f.encrypt(plaintext.encode())
        return ENCRYPTED_PREFIX + base64.urlsafe_b64encode(encrypted).decode()

    def decrypt(self, ciphertext: str) -> str:
        """解密密文，自动处理前缀标记"""
        if not ciphertext:
            return ""
        try:
            # 移除前缀标记
            if ciphertext.startswith(ENCRYPTED_PREFIX):
                ciphertext = ciphertext[len(ENCRYPTED_PREFIX):]
            key = self._get_or_create_key()
            f = Fernet(key)
            encrypted = base64.urlsafe_b64decode(ciphertext.encode())
            decrypted = f.decrypt(encrypted)
            return decrypted.decode()
        except Exception as e:
            raise RuntimeError(f"解密失败，密钥可能已变更或数据损坏: {e}")

    def is_encrypted(self, text: str) -> bool:
        """判断文本是否已加密（通过前缀标记识别）"""
        if not text:
            return False
        # 新版本：通过前缀标记识别
        if text.startswith(ENCRYPTED_PREFIX):
            return True
        # 兼容旧版本：启发式检测
        if text.startswith("<") or "://" in text[:10]:
            return False
        try:
            base64.urlsafe_b64decode(text.encode())
            return len(text) > 50 and "=" in text[-4:]
        except Exception:
            return False
