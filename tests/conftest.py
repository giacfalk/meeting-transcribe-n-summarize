import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="session")
def tk_root():
    """One Tcl interpreter for all GUI tests: creating and destroying several in one
    process is unreliable on Windows ("invalid command name tcl_findLibrary")."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display available ({exc})")
    root.withdraw()
    yield root
    root.destroy()
