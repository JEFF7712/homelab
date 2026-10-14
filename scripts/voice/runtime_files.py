from __future__ import annotations

from pathlib import Path


def remove_driver_stubs(store: Path) -> int:
    paths = list(store.rglob("libcuda.so*"))
    for path in paths:
        if path.is_file() or path.is_symlink():
            path.unlink()
        else:
            raise ValueError("Unexpected CUDA driver entry in retained SDK")
    return len(paths)


if __name__ == "__main__":
    count = remove_driver_stubs(Path("/nix/store"))
    if list(Path("/nix/store").rglob("libcuda.so*")):
        raise ValueError("Runtime image still contains CUDA driver stubs")
    print(f"Removed {count} SDK driver library entries")
