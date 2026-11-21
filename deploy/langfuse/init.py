"""Create private bootstrap settings once, without printing credential values."""

import os
import secrets
import uuid
from pathlib import Path


def main() -> None:
    path = Path(__file__).resolve().with_name(".env")
    if path.exists():
        print(f"{path} 已存在，沿用原有凭据。")
        return
    values = {
        "LANGFUSE_PORT": "3000",
        "LANGFUSE_BASE_URL": "http://langfuse-web:3000",
        "FAMSUN_LANGFUSE_ENABLED": "true",
        "FAMSUN_LANGFUSE_CAPTURE_CONTENT": "false",
        "LANGFUSE_INIT_ORG_ID": str(uuid.uuid4()),
        "LANGFUSE_INIT_PROJECT_ID": str(uuid.uuid4()),
        "LANGFUSE_INIT_USER_EMAIL": "admin@famsun.local",
        "LANGFUSE_PUBLIC_KEY": f"pk-lf-{uuid.uuid4()}",
        "LANGFUSE_SECRET_KEY": f"sk-lf-{secrets.token_hex(32)}",
    }
    for name in (
        "LANGFUSE_INIT_USER_PASSWORD",
        "NEXTAUTH_SECRET",
        "SALT",
        "ENCRYPTION_KEY",
        "POSTGRES_PASSWORD",
        "CLICKHOUSE_PASSWORD",
        "REDIS_AUTH",
        "MINIO_ROOT_PASSWORD",
    ):
        values[name] = secrets.token_hex(32)
    # Publish the complete file atomically; concurrent initializers cannot replace it.
    tmp = path.with_name(f".env.init-{uuid.uuid4().hex}")
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as env_file:
            for name, value in values.items():
                env_file.write(f"{name}={value}\n")
            env_file.flush()
            os.fsync(env_file.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            print(f"{path} 已存在，沿用原有凭据。")
            return
    finally:
        tmp.unlink(missing_ok=True)
    print(f"已生成 {path}（0600）。请将此文件存入受控的机密备份。")


if __name__ == "__main__":
    main()
