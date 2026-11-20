# How to run Jenga on your own computer

These steps run the Jenga website locally from VS Code. They were written for Windows; Mac
differences are noted where they matter.

## What you need

- **VS Code**
- **Python 3.10 or newer.** Check with `python --version` in a terminal.
- **The project code:** https://github.com/vincentx20072/oxai_making_sense_hackathon (use the `main` branch)
- **An API key.** The team's Groq key:

  ```
  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
  ```

## 1. Get the code

1. In VS Code, press `Ctrl+Shift+P` (`Cmd+Shift+P` on Mac) and choose **Git: Clone**.
2. Paste `https://github.com/vincentx20072/oxai_making_sense_hackathon` and pick a folder.
3. Click **Open** when VS Code asks. You'll be on the `main` branch.

To get the team's latest changes later, run `git pull` in the terminal.

## 2. Install the libraries

Open a terminal in VS Code (**Terminal → New Terminal**) and run:

```bash
pip install -r requirements.txt
```

If `pip` isn't found, use `python -m pip install -r requirements.txt` (on Mac, `python3 -m pip …`).

Warnings like *"The script flask.exe is installed in … which is not on PATH"* are harmless. You can
ignore them.

## 3. Add the API key

1. In VS Code's **Explorer** panel, right-click in the file list and choose **New File…**.
2. Name it exactly `.env`, with the dot at the start and no `.txt` ending.
3. Paste these two lines and save with `Ctrl+S`:

   ```
   AGENT_PROVIDER=groq
   GROQ_API_KEY=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
   ```

The file must sit **next to `app.py`**, not inside a subfolder. Don't put spaces around `=` or quotes
around the key.

**Optional: Claude instead of Groq.** If you have an Anthropic key (it starts `sk-ant-`), use this
single line instead of the two above. It switches the agent to Claude Opus 5.5 and turns on the Alibi
judge. A typical question then costs about $0.08.

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

## 4. Start the website

```bash
python app.py
```

On Mac use `python3 app.py`. When the terminal shows `Running on http://127.0.0.1:5000`, open
**http://localhost:5000** in your browser.

To stop the site, press `Ctrl+C` in the terminal. After any change to `.env`, stop the site and start
it again: the key is only read at start-up.

## Using it

- **New question:** upload PDFs (PDF only, up to 10 files and 25 MB), type a question, and run it.
  The page shows the agent's steps live.
- **Answer tab:** the answer with citation chips. Hover a chip to see the cited lines; click it to
  open the PDF page.
- **Alibi tab:** whether each claim's facts are in the cited lines. This needs an Anthropic key; with
  Groq only, it shows as unavailable.
- **Jenga tab:** the traceability score and each claim's tower of cited lines. **Pull the block**
  reruns the agent to test a claim, which costs several API calls, so use it sparingly.
- **Agent log tab:** every step the agent took, formatted or raw.
- **Analyse an existing log** (in the sidebar): score a `.log` file from an earlier run, with no API
  calls.

Runs are saved in `data/runs/` on your computer.

## Troubleshooting

**"API key is not set on the server, so the agent can't run"**

The app didn't find the key. Check these in order:

1. You restarted the app after creating `.env` (press `Ctrl+C`, then `python app.py`).
2. `.env` is in the same folder as `app.py`.
3. The file is called `.env`, not `.env.txt`.
4. With `.env` open, the bottom-right of VS Code says **UTF-8**, not **UTF-8 with BOM**. If it says
   BOM, click it, choose **Save with Encoding**, then **UTF-8**.

To see what the app finds, run this in the project folder:

```bash
python -c "import llm, os; print('provider:', llm.provider()); print('key found:', llm.has_credentials())"
```

**`python` isn't recognised**

Python isn't installed or isn't on PATH. Install it from https://www.python.org/downloads/ and tick
**Add python.exe to PATH** during the install. Then close and reopen VS Code.

**Port 5000 is already in use**

Another copy of the app is still running. Close the other terminal, or press `Ctrl+C` there first.

**Other people can't open the link**

`localhost` only works on the computer running the app. Sharing it with others needs hosting.
