# 8subagent-same-code — FullAgent v3.4.1

Terminal AI agent — ready-to-run, 8 provider keys built-in, no export needed.

## Source

- App source: [`aiagent/py.test/`](aiagent/py.test/) — `python main.py`
- Full docs: [`aiagent/py.test/README.md`](aiagent/py.test/README.md)

## Binary (Linux x64)

Built from `aiagent/py.test/fullagent.spec` via PyInstaller:

```bash
chmod +x fullagent-linux-x64
./fullagent-linux-x64 --version   # → 3.4.1
```

Also published under **Releases** (`v3.4.1+`) with Windows/macOS assets when CI builds them.

## Build it yourself

```bash
cd aiagent/py.test
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm fullagent.spec
./dist/fullagent --version
```
