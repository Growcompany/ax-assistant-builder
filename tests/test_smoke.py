import rag_app


def test_package_imports() -> None:
    assert rag_app.__version__ == "0.1.0"
