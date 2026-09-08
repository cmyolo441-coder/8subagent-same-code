# 8subagent-same-code — FullAgent v3.4.1

Terminal AI agent — ready-to-run, 8 provider keys built-in, no export needed.

## Source

- App source: [`aiagent/py.test/`](aiagent/py.test/) — `python main.py`
- Full docs: [`aiagent/py.test/README.md`](aiagent/py.test/README.md)

## Install (curl one-liner, Linux x64)

```bash
curl -fsSL https://github.com/cmyolo441-coder/8subagent-same-code/releases/latest/download/fullagent-linux-x64 -o fullagent \
  && chmod +x fullagent \
  && sudo mv fullagent /usr/local/bin/fullagent \
  && fullagent --version   # → 3.4.1
```

## Binary (Linux x64)

Built from `aiagent/py.test/fullagent.spec` via PyInstaller.
Binary repo me (`fullagent-linux-x64`) + **Releases** me bhi hai:

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
