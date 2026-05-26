# boots-agent

A small, educational **AI coding agent** built with Python and Google's Gemini API. Give it a natural-language prompt and it will autonomously explore a sandboxed project directory, read files, write or modify code, and run Python scripts — all by calling a small set of tools you've given it permission to use.

This repo ships with a tiny `calculator/` app as a demo "workspace" so you can immediately try the agent on something concrete (for example: *"fix the bug in the calculator's precedence handling"* or *"add a power operator to the calculator"*).

---

## Table of Contents

1. [What this project teaches](#what-this-project-teaches)
2. [Quick start](#quick-start)
3. [Usage examples](#usage-examples)
4. [How it works (high-level)](#how-it-works-high-level)
5. [Architecture in detail](#architecture-in-detail)
6. [The agent loop, step by step](#the-agent-loop-step-by-step)
7. [The four tools](#the-four-tools)
8. [Security: the working directory sandbox](#security-the-working-directory-sandbox)
9. [Project layout](#project-layout)
10. [Configuration](#configuration)
11. [Running the unit tests](#running-the-unit-tests)
12. [Extending the agent](#extending-the-agent)
13. [Troubleshooting](#troubleshooting)
14. [Glossary](#glossary)

---

## What this project teaches

If you're new to LLM-powered "agents", this codebase is a minimal but realistic example of the core pattern that powers tools like Cursor, Claude Code, and OpenAI's function-calling demos. By reading the code you'll learn:

- How a **Large Language Model (LLM)** can be turned into an *agent* by giving it **tools** (also called *functions*) it can call.
- How **function calling** works with the Google Gemini API (`google-genai` SDK).
- How to design a small **agent loop** that keeps calling the model until it produces a final answer.
- How to **sandbox** an agent so it can only touch files inside a designated working directory.
- How to keep a running **conversation history** that includes both the model's messages and tool results.

The whole thing is < 300 lines of Python, so it's easy to read end-to-end.

---

## Quick start

### 1. Prerequisites

- **Python 3.13+** (see `.python-version` / `pyproject.toml`)
- A **Google Gemini API key** — get one free from [Google AI Studio](https://aistudio.google.com/app/apikey).
- Recommended: [`uv`](https://docs.astral.sh/uv/) for fast, reproducible installs. `pip` works too.

### 2. Clone and install

```bash
git clone <your-fork-url> boots-agent
cd boots-agent

# Using uv (recommended — uv.lock is included)
uv sync

# Or using pip
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install "google-genai==1.12.1" "python-dotenv==1.1.0"
```

### 3. Add your API key

Create a `.env` file in the project root:

```bash
echo 'GEMINI_API_KEY=your_key_here' > .env
```

`.env` is git-ignored, so your key stays local.

### 4. Run the agent

```bash
uv run main.py "list the files in the calculator project and explain what it does"
```

(or `python main.py "..."` if you're using a regular venv)

Add `--verbose` to see every tool call, token count, and intermediate response:

```bash
uv run main.py --verbose "run the tests in the calculator and tell me if they pass"
```

---

## Usage examples

The agent works on whatever directory is configured as `WORKING_DIR` in `config.py` — by default, the bundled `calculator/`. Try prompts like:

- `"What files are in this project? Give me a quick summary of each."`
- `"Run the unit tests and tell me which ones pass."`
- `"There's a bug somewhere in the calculator. Find it and fix it."`
- `"Add a new operator '%' (modulo) to the calculator and update the tests."`
- `"Refactor render.py to also support a plain-text output format, controlled by a parameter."`

Behind the scenes the agent will: list files → read the relevant ones → run them if needed → edit them → re-run to verify → report back to you.

---

## How it works (high-level)

At its core the agent is a small loop around a single function call:

```
              ┌───────────────────────────────┐
   prompt ──▶ │   send conversation to Gemini │
              └───────────────┬───────────────┘
                              │
              ┌───────────────▼───────────────┐
              │  Did the model call a tool?   │
              └───────┬───────────────┬───────┘
                  no  │               │ yes
                      ▼               ▼
              print final     run the tool locally,
              response &      append its output to
              exit            the conversation,
                              loop again
```

Each iteration the LLM sees:
- the original user prompt,
- everything it has said so far,
- and the results of every tool call it has made.

This is how it can do multi-step work like "open three files, find the bug, write a fix, and run the tests" from a single prompt.

---

## Architecture in detail

The repo is split into four conceptual layers:

| Layer                | Files                                | Responsibility |
|----------------------|--------------------------------------|----------------|
| **Entry point**      | `main.py`                            | Parse CLI args, set up the Gemini client, run the agent loop. |
| **Tool dispatcher**  | `call_function.py`                   | Maps tool names the LLM emits to real Python functions; injects the sandbox directory. |
| **Tools**            | `functions/*.py`                     | Concrete implementations: list files, read file, run python, write file. Each file also exports a `Schema` that tells Gemini how to call it. |
| **Prompt / config**  | `prompts.py`, `config.py`            | The system prompt that gives the agent its "personality" and the constants that define the sandbox. |
| **Demo workspace**   | `calculator/`                        | A sample app the agent operates on — the sandbox. |

### `main.py` — the entry point

```13:35:main.py
def main():
    parser = argparse.ArgumentParser(description="AI Code Assistant")
    parser.add_argument("user_prompt", type=str, help="Prompt to send to Gemini")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable not set")

    client = genai.Client(api_key=api_key)
    messages = [types.Content(role="user", parts=[types.Part(text=args.user_prompt)])]
    if args.verbose:
        print(f"User prompt: {args.user_prompt}\n")

    for _ in range(20):
        result = generate_content(client, messages, args.verbose)
        if result:
            break
    else:
        print("Maximum iterations (20) reached without a final response.")
        sys.exit(1)
```

Key things to notice:

- `messages` is a list of `Content` objects — this is the **conversation history**. We seed it with the user's prompt.
- The loop runs at most **20 iterations**. This is a safety net: if the model gets stuck in a tool-calling loop, we bail instead of burning your API quota.
- `generate_content(...)` returns `True` when the model has produced a normal text answer (we're done) and `False` when it called a tool (we need another iteration so the model can react to the tool's result).

### `generate_content` — one turn of the loop

```38:81:main.py
def generate_content(client, messages, verbose):
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=messages,
        config=types.GenerateContentConfig(
            tools=[available_functions], system_instruction=system_prompt
        ),
    )

    if response.candidates:
        for candidate in response.candidates:
            if candidate.content:
                messages.append(candidate.content)

    if not response.usage_metadata:
        raise RuntimeError("Gemini API response appears to be malformed")

    if verbose:
        print("Prompt tokens:", response.usage_metadata.prompt_token_count)
        print("Response tokens:", response.usage_metadata.candidates_token_count)

    if not response.function_calls:
        print("Response:")
        print(response.text)
        return True

    function_responses = []
    for function_call in response.function_calls:
        result = call_function(function_call, verbose)
        if (
            not result.parts
            or not result.parts[0].function_response
            or not result.parts[0].function_response.response
        ):
            raise RuntimeError(f"Empty function response for {function_call.name}")
        if verbose:
            print(f"-> {result.parts[0].function_response.response}")
        function_responses.append(result.parts[0])

    if not function_responses:
        raise RuntimeError("No function responses generated, exiting.")

    messages.append(types.Content(role="user", parts=function_responses))
    return False
```

What's happening:

1. We send the whole conversation plus the **tool schemas** (`available_functions`) and the **system prompt** to Gemini.
2. Gemini's response always becomes part of the conversation, whether it's text or a tool call — so the model sees its own reasoning later.
3. If there are **no function calls**, the model has produced a normal answer. Print it and return `True`.
4. If there **are** function calls, we run them locally with `call_function(...)`, then append all the results back into `messages` under the `"user"` role. The next loop iteration shows those results to the model.

---

## The agent loop, step by step

Suppose you run:

```bash
uv run main.py "what does main.py in the calculator do?"
```

The loop typically goes:

1. **Iteration 1** — Gemini receives the prompt. It doesn't know what's in `main.py`, so it emits a tool call: `get_file_content(file_path="main.py")`. We run it, append the file contents to the conversation. Loop continues.
2. **Iteration 2** — Gemini sees the file contents and now has enough information. It emits a normal text response describing what `main.py` does. We print it and exit.

For a debugging task ("find and fix the bug"), the loop typically grows to 5–10 iterations: list files → read several files → run the tests → write a fixed file → re-run the tests → final summary.

The `--verbose` flag is great for actually *seeing* this loop in motion — try it.

---

## The four tools

The agent is only as powerful as the tools you give it. This project ships with four, each defined in `functions/` as a pair of *(Python function, Gemini schema)*.

| Tool                | What it does                                                   | Required args         |
|---------------------|----------------------------------------------------------------|-----------------------|
| `get_files_info`    | Lists files in a directory; reports size and `is_dir`.         | *(none — defaults to `.`)* |
| `get_file_content`  | Reads up to `MAX_CHARS` (10 000) characters of a text file.    | `file_path`           |
| `run_python_file`   | Runs a `.py` file via `subprocess`, captures stdout/stderr.    | `file_path` (+ optional `args`) |
| `write_file`        | Writes/overwrites a text file; creates parent dirs as needed.  | `file_path`, `content` |

They are registered together in `call_function.py`:

```9:23:call_function.py
available_functions = types.Tool(
    function_declarations=[
        schema_get_files_info,
        schema_get_file_content,
        schema_run_python_file,
        schema_write_file,
    ]
)

function_map = {
    "get_files_info": get_files_info,
    "get_file_content": get_file_content,
    "run_python_file": run_python_file,
    "write_file": write_file,
}
```

`available_functions` is what we hand to Gemini so it knows *what tools exist and how to call them*. `function_map` is what we use locally to actually invoke the right Python function when Gemini emits a call.

### Why each tool has a "schema"

Gemini doesn't read your Python source. It only knows about your tools through the **schema** you declare. For example:

```28:40:functions/get_files_info.py
schema_get_files_info = types.FunctionDeclaration(
    name="get_files_info",
    description="Lists files in a specified directory relative to the working directory, providing file size and directory status",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "directory": types.Schema(
                type=types.Type.STRING,
                description="Directory path to list files from, relative to the working directory (default is the working directory itself)",
            ),
        },
    ),
)
```

The `description` fields are how the model decides *when* to use a tool, and the `parameters` block tells it the exact shape of the arguments to pass. Clear, accurate descriptions are the single biggest factor in agent quality.

---

## Security: the working directory sandbox

The agent is given file-system access — that's the whole point — but it should never be able to read `/etc/passwd` or write outside the project. Every tool defends against this the same way:

```7:14:functions/get_file_content.py
def get_file_content(working_directory, file_path):
    try:
        abs_working_dir = os.path.abspath(working_directory)
        abs_file_path = os.path.normpath(os.path.join(abs_working_dir, file_path))
        if os.path.commonpath([abs_working_dir, abs_file_path]) != abs_working_dir:
            return f'Error: Cannot read "{file_path}" as it is outside the permitted working directory'
```

The pattern is:

1. Resolve the working directory to an **absolute path**.
2. Join the user-supplied (LLM-supplied!) `file_path` onto it and normalize (`os.path.normpath` collapses `..`, etc.).
3. Use `os.path.commonpath` to verify the result still lives **inside** the working directory.

Crucially, the LLM **does not get to choose the working directory** — it's injected by `call_function.py` from `config.WORKING_DIR`:

```44:46:call_function.py
    args = dict(function_call.args) if function_call.args else {}
    args["working_directory"] = WORKING_DIR
    result = function_map[function_name](**args)
```

So even if the model tries to pass `working_directory="/etc"`, that argument gets overwritten before the function is called. The system prompt also tells the model not to bother:

```28:30:prompts.py
All file paths you provide in function calls must be relative to the
working directory. Do not include the working directory itself in the
paths; it is injected automatically for security reasons.
```

> ⚠️ **Caveats.** This sandbox stops path traversal. It does **not** stop a malicious script being written and then executed via `run_python_file` — that script will run with the same permissions as your user. Don't point this agent at a directory you don't trust, and don't expose it to untrusted users.

---

## Project layout

```
boots-agent/
├── main.py                  # Entry point + agent loop
├── call_function.py         # Tool registry & dispatcher
├── config.py                # MAX_CHARS, WORKING_DIR
├── prompts.py               # System prompt
│
├── functions/               # The tools
│   ├── get_files_info.py
│   ├── get_file_content.py
│   ├── run_python_file.py
│   └── write_file.py
│
├── calculator/              # Demo workspace the agent operates on
│   ├── main.py              # CLI entry point: `python main.py "3 + 5"`
│   ├── tests.py             # unittest suite
│   ├── pkg/
│   │   ├── calculator.py    # Infix-expression evaluator (shunting-yard-ish)
│   │   └── render.py        # JSON output formatter
│   └── lorem.txt            # Filler file used by tests
│
├── test_get_files_info.py   # Manual smoke tests for the tools
├── test_get_file_content.py
├── test_run_python_file.py
├── test_write_file.py
│
├── .env                     # Your GEMINI_API_KEY (git-ignored)
├── pyproject.toml           # Project metadata + dependencies
└── uv.lock                  # Pinned dependency versions
```

---

## Configuration

Everything tweakable lives in `config.py`:

```1:2:config.py
MAX_CHARS = 10000
WORKING_DIR = "./calculator"
```

- **`MAX_CHARS`** — maximum number of characters `get_file_content` will return. Larger files are truncated with a notice appended, so the model knows there's more it didn't see. This keeps the context window (and your bill) under control.
- **`WORKING_DIR`** — the sandbox the agent is allowed to touch. Point this at any directory you want the agent to work on.

The Gemini model is hard-coded in `main.py` as `gemini-2.5-flash`. Swap it for `gemini-2.5-pro` for higher-quality (and pricier) reasoning.

---

## Running the unit tests

The bundled calculator has its own unit-test suite, runnable directly:

```bash
cd calculator
python tests.py
```

The smoke tests at the repo root exercise the tool layer (they're not `unittest`-style — they just print results):

```bash
python test_get_files_info.py
python test_get_file_content.py
python test_run_python_file.py
python test_write_file.py
```

Each test deliberately probes both happy paths and security boundaries (e.g. reading `/bin/cat`, writing to `/tmp/temp.txt`) so you can see the sandbox in action.

---

## Extending the agent

Want to give the agent a new capability? It's three steps:

1. **Write the function.** Create a new file in `functions/`, e.g. `functions/delete_file.py`. The function must take `working_directory` as its first argument and apply the same `commonpath` sandbox check you'll find in the existing tools.
2. **Declare its schema.** In the same file, export a `types.FunctionDeclaration` (see any existing tool for a template). Be specific in the `description` — that's how the model learns when to use it.
3. **Register it.** Add the schema to `available_functions` and the function to `function_map` in `call_function.py`.

That's it — the next time you run the agent, Gemini will see the new tool listed alongside the others.

Other ideas:
- Swap `gemini-2.5-flash` for an OpenAI or Anthropic model — the agent loop pattern is the same; only the SDK calls change.
- Add a `--max-iterations` CLI flag instead of the hard-coded `20`.
- Persist `messages` to disk so you can resume a session.
- Stream responses instead of waiting for the whole reply each turn.

---

## Troubleshooting

| Symptom                                                | Likely cause / fix |
|--------------------------------------------------------|--------------------|
| `RuntimeError: GEMINI_API_KEY environment variable not set` | `.env` is missing or the key name is wrong. Make sure the file contains `GEMINI_API_KEY=...`. |
| `403` / `permission denied` from Gemini                | Your API key is invalid or your project doesn't have Gemini API access enabled in Google AI Studio. |
| `Maximum iterations (20) reached without a final response.` | The model is stuck in a tool-calling loop. Try a more specific prompt, or bump the loop limit in `main.py`. |
| `Error: "..." is not a directory` / `... outside the permitted working directory` | Expected: the sandbox is blocking a path. Check `WORKING_DIR` in `config.py`. |
| Output is truncated with `[...File "..." truncated at 10000 characters]` | Working as designed. Raise `MAX_CHARS` in `config.py` if you really need it. |

---

## Glossary

- **LLM (Large Language Model)** — the underlying neural network, e.g. Gemini 2.5 Flash, that produces text from text.
- **Agent** — an LLM wrapped in a loop that lets it take actions (call tools) and react to their results, rather than just answering once.
- **Tool / Function calling** — the LLM API feature that lets the model emit a structured "I want to call `foo(x=1)`" message instead of plain text. Your program runs `foo` and feeds the result back.
- **Schema** — a JSON-like description of a tool's name, purpose, and parameters. The model uses it to decide when and how to call the tool.
- **System prompt** — instructions that sit "above" the conversation and shape how the model behaves on every turn. See `prompts.py`.
- **Conversation history (`messages`)** — the running list of everything the user, the model, and the tools have said, in order. Sent in full on every API call so the model has full context.
- **Sandbox / working directory** — the single directory the agent is allowed to read, write, or execute inside. Any path resolving outside it is rejected.

---