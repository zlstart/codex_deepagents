"""Trusted one-shot reader, run from the image with only the session data volume."""

import datetime as dt
import json
import mimetypes
import os
import stat
import sys

MAX_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 5000
DIRECTORY = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY


def visible(component):
    return (
        not component.startswith(".")
        and component.isprintable()
        and "\\" not in component
    )


def list_files():
    files = []
    visited = 0
    # fwalk uses descriptor-relative traversal without following symlinks.
    for folder, folders, names, parent in os.fwalk("/workspace", follow_symlinks=False):
        folders[:] = sorted(d for d in folders if visible(d))
        visited += len(folders) + len(names)
        if visited > MAX_ENTRIES:
            raise OverflowError("workspace contains too many entries")
        for name in names:
            if not visible(name):
                continue
            try:
                meta = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(meta.st_mode):
                continue
            relative = os.path.relpath(os.path.join(folder, name), "/workspace")
            if len(relative) > 1024:
                continue
            files.append(
                {
                    "path": relative,
                    "name": name,
                    "size": meta.st_size,
                    "modified_at": dt.datetime.fromtimestamp(
                        meta.st_mtime, dt.UTC
                    ).isoformat(),
                    "mime_type": mimetypes.guess_type(name)[0]
                    or "application/octet-stream",
                }
            )
    print(json.dumps(sorted(files, key=lambda f: f["path"])))


def read_file(path):
    parts = path.split("/")
    if not all(part and not part.startswith(".") for part in parts):
        raise ValueError("invalid artifact path")
    directory = os.open("/workspace", DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode):
                raise ValueError("artifact must be a regular file")
            if meta.st_size > MAX_BYTES:
                raise OverflowError("artifact exceeds transfer limit")
            data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise OverflowError("artifact exceeds transfer limit")
            sys.stdout.buffer.write(data)
    finally:
        os.close(directory)


if __name__ == "__main__":
    try:
        if sys.argv[1] == "list":
            list_files()
        else:
            read_file(sys.argv[2])
    except FileNotFoundError:
        sys.exit(42)
    except OverflowError:
        sys.exit(43)
    except (OSError, ValueError, RecursionError):
        sys.exit(44)
