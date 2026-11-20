"""Owner-operated account provisioning for deployments with closed registration."""

import argparse
import getpass
import sys

from .auth import USERNAME, password_hash
from .config import Settings
from .store import Conflict, Store


def main():
    parser = argparse.ArgumentParser(description="FAMSUN 帐户管理")
    parser.add_argument("command", choices=["create-user"])
    parser.add_argument("username")
    args = parser.parse_args()
    if not USERNAME.fullmatch(args.username):
        parser.error("用户名需为 3 到 64 个 ASCII 字符，以字母或数字开头，可包含 ._-")
    password = getpass.getpass("Password (12–128 characters): ")
    if not 12 <= len(password) <= 128 or password != getpass.getpass(
        "Confirm password: "
    ):
        parser.exit(2, "密码长度无效或两次输入不一致\n")
    root = Settings().data_dir
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = Store(root / "studio.sqlite3")
    db.initialize()
    try:
        user = db.create_user(args.username, password_hash(password))
    except Conflict as exc:
        sys.exit(str(exc))
    print(f"Created {user['username']}")


if __name__ == "__main__":
    main()
