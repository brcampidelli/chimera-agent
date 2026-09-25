"""Thirty-six short spoken requests that tempt output a voice cannot read (PREREGISTRATION.md).

Written as a speech-to-text transcript would give them: whole sentences, punctuation, digits as
digits. Each one is answerable without tools or a workspace, so what is measured is the form of the
answer and nothing else. Eighteen in Portuguese, eighteen in English; seven kinds.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Request:
    id: str
    kind: str
    lang: str
    text: str


KINDS = ("files", "command", "code", "url", "numbers", "compare", "chat")

_ROWS: tuple[tuple[str, str, str, str], ...] = (
    # "list the files that…" — tempts a bulleted list of file names and paths
    ("files-01", "files", "pt", "Quais arquivos de configuração um projeto Next.js novo costuma ter na raiz?"),
    ("files-02", "files", "en", "List the files I need to add to a Python package so it can be published on PyPI."),
    ("files-03", "files", "pt", "Me lista o que eu devo colocar no .gitignore de um projeto Node."),
    ("files-04", "files", "en", "Which files in a typical Django project hold the settings, the URLs and the models?"),
    ("files-05", "files", "pt", "Quais arquivos o Docker precisa pra subir uma aplicação com compose?"),
    ("files-06", "files", "en", "Where do GitHub Actions workflows live in a repo, and what are the usual files there?"),
    # "what's the command to…" — tempts a code block or a flag-heavy command line
    ("command-01", "command", "en", "What's the command to find all files bigger than 100 megabytes on Linux?"),
    ("command-02", "command", "pt", "Qual é o comando pra desfazer o último commit sem perder as alterações?"),
    ("command-03", "command", "en", "What's the command to see which process is using port 8000?"),
    ("command-04", "command", "pt", "Qual o comando pra criar um ambiente virtual do Python e ativar ele no Windows?"),
    ("command-05", "command", "en", "How do I stop all running Docker containers at once? What's the command?"),
    ("command-06", "command", "pt", "Como eu vejo o tamanho de cada pasta no terminal? Qual comando?"),
    # "show me the code for…" — tempts a fenced block
    ("code-01", "code", "en", "Show me the code for a Python function that checks if a string is a palindrome."),
    ("code-02", "code", "pt", "Me mostra o código de um componente React que conta cliques num botão."),
    ("code-03", "code", "en", "Show me how to read a JSON file in Node, the code."),
    ("code-04", "code", "pt", "Mostra o SQL pra pegar os dez clientes que mais compraram."),
    ("code-05", "code", "pt", "Me dá uma regex que valida um endereço de e-mail."),
    # "which URL…" — tempts a raw URL
    ("url-01", "url", "en", "Which URL do I open to create a GitHub personal access token?"),
    ("url-02", "url", "pt", "Qual é o endereço da documentação oficial do Supabase sobre RLS?"),
    ("url-03", "url", "en", "What's the URL for the OpenRouter models list?"),
    ("url-04", "url", "pt", "Qual o link pra baixar o Python no site oficial?"),
    ("url-05", "url", "en", "Where's the Next.js docs page about the App Router? Give me the link."),
    # numbers, dates and versions — tempts long digit strings, ISO dates, dotted versions, addresses
    ("numbers-01", "numbers", "en", "What's the current LTS version of Node.js, and when does it reach end of life?"),
    ("numbers-02", "numbers", "pt", "Quantos segundos tem um ano? Me dá o número exato."),
    ("numbers-03", "numbers", "en", "What are the private IP address ranges, the ones starting with 10 and 192?"),
    ("numbers-04", "numbers", "pt", "Qual foi a data exata de lançamento do Python 3?"),
    ("numbers-05", "numbers", "en", "What's 2 to the power of 32?"),
    # "compare X and Y" — tempts a table
    ("compare-01", "compare", "pt", "Compara pra mim Postgres e MySQL pra um SaaS pequeno."),
    ("compare-02", "compare", "en", "Compare React and Vue. What are the main differences?"),
    ("compare-03", "compare", "pt", "Qual a diferença entre let, const e var no JavaScript?"),
    ("compare-04", "compare", "en", "Compare the free plans of Vercel and Netlify."),
    ("compare-05", "compare", "pt", "Me compara Python e Go pra escrever uma API."),
    # plain chit-chat — the control: nothing here tempts structure
    ("chat-01", "chat", "en", "Are you understanding me?"),
    ("chat-02", "chat", "pt", "Bom dia! Tudo certo por aí?"),
    ("chat-03", "chat", "en", "Thanks, that's all for now."),
    ("chat-04", "chat", "pt", "Você tá me ouvindo bem?"),
)


def corpus() -> list[Request]:
    return [Request(*row) for row in _ROWS]
