#!/usr/bin/env python3
"""chimera_blog_digest.py — o boletim de IA do chimeraagent.space, duas vezes por dia.

Roda no sidecar (uid 10000, sem git, sem dependências fora da stdlib). Publica pela API de
conteúdo do GitHub: nada é clonado, o que também respeita o disco da VPS, que está em 78%.

O DESENHO, EM UMA FRASE
    O post não pode afirmar mais do que a fonte diz, porque a manchete, o veículo, a data e o link
    vão para o frontmatter e a página os renderiza de lá. Sobra ao modelo exatamente um campo por
    item — `comment` —, marcado como nosso e limitado a 400 caracteres pelo portão do site.

O QUE ESTE SCRIPT RECUSA A FAZER
    * Confiar na data relativa da listagem. "Há 20 horas" não é uma data; a data sai do
      `datePublished` no JSON-LD do próprio artigo. Foi montando o boletim de estreia à mão que
      isso apareceu, e é o campo cuja honestidade sustenta o resto.
    * Publicar a manchete que veio da busca. A manchete é a que o artigo declara em `og:title` ou
      `<title>` — um resumo de buscador é texto de terceiro sobre o texto do veículo.
    * Reproduzir corpo de matéria. O que sai daqui é manchete (título, citação padrão), link e
      comentário próprio. A `description` do artigo entra como insumo do modelo e nunca é publicada.
    * Inventar volume. Se nenhum candidato passa, nada é publicado — um boletim vazio é uma página
      com data e mais nada, e o portão do site o reprovaria de qualquer jeito.

Uso:
    chimera_blog_digest.py [--slot meio-dia|fim-do-dia] [--dry-run] [--max-items 3]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

REPO = "brcampidelli/chimera-site"
SITE = "https://chimeraagent.space"
SEEN_PATH = "/opt/data/state/blog_digest_seen.json"
LOG_PATH = "/opt/data/logs/blog-digest.log"

UA = {"User-Agent": f"Mozilla/5.0 (compatible; ChimeraDigest/1.0; +{SITE})"}

# As seis do Bruno, mais nove em inglês.
#
# As brasileiras sozinhas rendiam três itens publicáveis num dia com 32 candidatos: só a Exame
# cobre IA diariamente. A escolha das novas foi medida pelo que decide o boletim — quantos itens
# cada feed tem DENTRO da janela de 72h, não quantos itens tem:
#
#     TechCrunch 13 · The Decoder 10 · The Verge 6 · Simon Willison 6 · Ars Technica 4
#     OpenAI 2 · Latent Space 2 · Hugging Face 1
#
# Ficaram de fora por medição, não por gosto: VentureBeat (mais novo com 1972h — o feed está
# parado), MIT Tech Review (147h), Google DeepMind (78h, logo fora) e MarkTechPost (403 para
# qualquer cliente que não seja navegador). Vale remedir de vez em quando; um feed que parou pode
# voltar, e um que publica hoje pode parar sem avisar.
FEEDS = [
    ("IA Expert Academy", "https://iaexpert.academy/blog/feed/"),
    ("IA Brasil Notícias", "https://iabrasilnoticias.com.br/feed/"),
    ("Exame", "https://exame.com/feed/"),
    ("InfoMoney", "https://www.infomoney.com.br/feed/"),
    ("NeoFeed", "https://neofeed.com.br/feed/"),
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Decoder", "https://the-decoder.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("Ars Technica", "https://arstechnica.com/ai/feed/"),
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Latent Space", "https://www.latent.space/feed"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
]
OPENROUTER_BLOG = ("OpenRouter", "https://openrouter.ai/blog")

# Só publica o que vem daqui. Uma busca devolve o que quiser; a lista decide o que é fonte.
ALLOWED_HOSTS = {
    "iaexpert.academy",
    "iabrasilnoticias.com.br",
    "exame.com",
    "www.infomoney.com.br",
    "infomoney.com.br",
    "neofeed.com.br",
    "openrouter.ai",
    "techcrunch.com",
    "the-decoder.com",
    "www.theverge.com",
    "theverge.com",
    "simonwillison.net",
    "arstechnica.com",
    "openai.com",
    "www.latent.space",
    "huggingface.co",
}

# Exame, InfoMoney e NeoFeed publicam de tudo num feed só. Isto é o filtro de assunto.
TOPIC = re.compile(
    r"intelig[êe]ncia artificial|\bia\b|\bai\b|\bllm\b|\bgpt\b|openai|anthropic|claude|gemini|"
    r"deepseek|mistral|llama|qwen|kimi|modelo de linguagem|agente[s]? de ia|ai agent|"
    r"machine learning|aprendizado de m[áa]quina|redes neurais|chatbot|copilot|nvidia",
    re.I,
)

# 72h, não 48. Medido: numa quarta à noite as seis fontes juntas ofereciam UM item dentro de 48h
# que passasse no filtro de assunto — só a Exame publica IA todo dia. A janela é o que decide se o
# boletim sai; a data de cada item aparece no card, então quem lê julga a idade por conta própria.
MAX_AGE_HOURS = int(os.environ.get("CHIMERA_DIGEST_MAX_AGE_H", "72"))
COMMENT_MAX = 400  # o mesmo teto que o portão do site aplica
FETCH_TIMEOUT = 25


# --------------------------------------------------------------------------- infraestrutura

def log(msg: str) -> None:
    line = f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def env(key: str) -> str | None:
    val = os.environ.get(key)
    if val:
        return val
    try:
        with open("/opt/data/.env", encoding="utf-8") as handle:
            for line in handle:
                m = re.match(r"\s*(?:export\s+)?" + re.escape(key) + r"\s*=\s*(.*)", line.strip())
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def get(url: str, timeout: int = FETCH_TIMEOUT, max_bytes: int = 400_000) -> tuple[int, str, str]:
    """(status, corpo, url final). O url final importa: redirecionamento muda o domínio.

    `max_bytes` existe porque uma página de artigo não precisa ser lida inteira — mas um FEED
    precisa: o da Hugging Face traz 835 itens e o da OpenAI 1115, e cortar XML no meio produz um
    ParseError que só apareceria no log como "esta fonte não respondeu". Feed lê com folga.
    """
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.status, raw.decode(charset, "replace"), resp.geturl()


# --------------------------------------------------------------------------- coleta

def from_feeds() -> list[dict]:
    out = []
    for outlet, url in FEEDS:
        try:
            _, body, _ = get(url, max_bytes=6_000_000)
            root = ET.fromstring(body)
        except Exception as exc:  # noqa: BLE001 - uma fonte fora do ar não derruba o boletim
            log(f"feed {outlet}: {type(exc).__name__} {str(exc)[:70]}")
            continue
        for item in root.iter():
            tag = item.tag.split("}")[-1]
            if tag not in ("item", "entry"):
                continue
            title = _text(item, ("title",))
            link = _link(item)
            if not link:
                continue
            cats = " ".join(_all_text(item, ("category",)))
            if not TOPIC.search(f"{title} {cats} {link}"):
                continue
            # A data vem do feed, e é aqui que ela tem de vir.
            #
            # A primeira versão exigia `datePublished` no JSON-LD do artigo, porque foi assim que
            # peguei as datas do boletim de estreia — no navegador. Fora dele a Exame devolve 43 KB
            # de casca, sem data alguma: o JSON-LD é montado por JavaScript. Quatorze candidatos
            # foram recusados por "sem datePublished" enquanto a data estava, o tempo todo, no
            # `pubDate` do feed que já tínhamos baixado. O feed é a declaração do próprio veículo;
            # não é um substituto pior, é a mesma fonte, um fetch mais barato.
            out.append(
                {
                    "outlet": outlet,
                    "url": link,
                    "hint": title,
                    "feed_date": _text(item, ("pubDate", "published", "updated", "date")),
                    "feed_desc": re.sub(r"<[^>]+>", " ", _text(item, ("description", "summary"))),
                }
            )
    return out


def from_openrouter() -> list[dict]:
    outlet, url = OPENROUTER_BLOG
    try:
        _, body, _ = get(url)
    except Exception as exc:  # noqa: BLE001
        log(f"html {outlet}: {type(exc).__name__} {str(exc)[:70]}")
        return []
    slugs = set(re.findall(r'href="(/blog/[a-z0-9\-]{6,})"', body))
    return [{"outlet": outlet, "url": f"https://openrouter.ai{s}", "hint": ""} for s in slugs]


def from_search() -> list[dict]:
    """Tavily, restrita aos domínios da lista. A busca amplia a colheita, nunca a permissão."""
    key = env("TAVILY_API_KEY")
    if not key:
        return []
    payload = {
        "api_key": key,
        "query": "notícias inteligência artificial LLM agentes de IA",
        "search_depth": "basic",
        "topic": "news",
        "days": 2,
        "max_results": 12,
        "include_domains": sorted(ALLOWED_HOSTS),
    }
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode(),
        headers={**UA, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        log(f"tavily: {type(exc).__name__} {str(exc)[:70]}")
        return []
    # `hint` fica vazio de propósito: o título publicável sai do artigo, não do buscador.
    return [{"outlet": "", "url": r.get("url", ""), "hint": ""} for r in data.get("results", [])]


def _text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in node:
        if child.tag.split("}")[-1] in names and (child.text or "").strip():
            return " ".join((child.text or "").split())
    return ""


def _all_text(node: ET.Element, names: tuple[str, ...]) -> list[str]:
    return [
        " ".join((c.text or "").split())
        for c in node
        if c.tag.split("}")[-1] in names and (c.text or "").strip()
    ]


def _link(node: ET.Element) -> str:
    for child in node:
        if child.tag.split("}")[-1] != "link":
            continue
        if (child.text or "").strip().startswith("http"):
            return child.text.strip()
        href = child.get("href", "")
        if href.startswith("http"):
            return href
    return ""


# --------------------------------------------------------------------------- verificação

JSONLD_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')
META_DATE = re.compile(
    r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)', re.I
)
OG_TITLE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', re.I)
HTML_TITLE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I)
OG_DESC = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=["\']([^"\']+)',
    re.I,
)


def unescape(text: str) -> str:
    import html

    return " ".join(html.unescape(text).split())


def verify(cand: dict) -> tuple[dict | None, str]:
    """Abre o artigo. Devolve (item, motivo-da-recusa). Toda recusa tem um motivo dizível."""
    url = cand["url"]
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host not in ALLOWED_HOSTS:
        return None, f"fora da lista de fontes ({host})"
    # A busca devolve páginas de seção junto com matérias. Uma listagem não tem data nem manchete
    # próprias, e recusá-la pelo caminho diz mais do que recusá-la por falta de data.
    if re.search(r"/(tudo-sobre|noticias-sobre|categoria|tag|topico)/", parsed.path):
        return None, "página de seção, não matéria"

    try:
        status, body, final = get(url)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    if status != 200:
        return None, f"HTTP {status}"

    final_host = urllib.parse.urlparse(final).netloc.lower()
    if final_host not in ALLOWED_HOSTS:
        return None, f"redirecionou para fora da lista ({final_host})"

    # Ordem deliberada: o feed primeiro. Ver o comentário em `from_feeds`.
    raw = cand.get("feed_date") or ""
    if not raw:
        m = JSONLD_DATE.search(body) or META_DATE.search(body)
        raw = m.group(1) if m else ""
    if not raw:
        return None, "sem data declarada (nem no feed nem no artigo)"
    published = parse_date(raw)
    if not published:
        return None, f"data ilegível ({raw[:24]})"

    age = datetime.now(UTC) - published
    if age > timedelta(hours=MAX_AGE_HOURS):
        return None, f"publicado há {int(age.total_seconds() // 3600)}h"
    if age < timedelta(hours=-6):
        return None, "data no futuro"

    title_m = OG_TITLE.search(body) or HTML_TITLE.search(body)
    # A manchete do feed é do veículo tanto quanto a da página. O que nunca vale é a do buscador,
    # e por isso `hint` fica vazio nos candidatos vindos de busca.
    headline = unescape(title_m.group(1)) if title_m else unescape(cand.get("hint") or "")
    if not headline:
        return None, "sem título"
    headline = re.sub(
        r"\s*[|–-]\s*(Exame|InfoMoney|NeoFeed|OpenRouter|TechCrunch|The Decoder|The Verge|"
        r"Ars Technica|OpenAI|Latent Space|Hugging Face|Simon Willison)\s*$",
        "",
        headline,
    ).strip()
    if len(headline) < 15:
        return None, "título curto demais para ser manchete"

    desc_m = OG_DESC.search(body)
    description = unescape(desc_m.group(1)) if desc_m else unescape(cand.get("feed_desc") or "")
    return (
        {
            "headline": headline,
            "url": final,
            "outlet": cand.get("outlet") or outlet_of(final_host),
            "published": published.strftime("%Y-%m-%d"),
            "published_at": published,
            "description": description[:600],
        },
        "",
    )


def parse_date(value: str) -> datetime | None:
    """RSS fala RFC 822 ("Sat, 09 Aug 2026 18:51:45 +0000"), JSON-LD fala ISO 8601, e um `<time>`
    às vezes fala só a data. Aceita os três, e devolve SEMPRE com fuso — um datetime ingênuo
    subtraído de um com fuso levanta TypeError no meio da rodada, que foi como isto apareceu."""
    value = (value or "").strip()
    if not value:
        return None

    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(value)
        if dt:
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        pass

    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value.replace("Z", "+0000"), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def outlet_of(host: str) -> str:
    return {
        "iaexpert.academy": "IA Expert Academy",
        "iabrasilnoticias.com.br": "IA Brasil Notícias",
        "exame.com": "Exame",
        "www.infomoney.com.br": "InfoMoney",
        "infomoney.com.br": "InfoMoney",
        "neofeed.com.br": "NeoFeed",
        "openrouter.ai": "OpenRouter",
        "techcrunch.com": "TechCrunch",
        "the-decoder.com": "The Decoder",
        "www.theverge.com": "The Verge",
        "theverge.com": "The Verge",
        "simonwillison.net": "Simon Willison",
        "arstechnica.com": "Ars Technica",
        "openai.com": "OpenAI",
        "www.latent.space": "Latent Space",
        "huggingface.co": "Hugging Face",
    }.get(host, host)


# --------------------------------------------------------------------------- comentário

PROMPT = """Você escreve uma linha de comentário para um boletim de notícias de IA do projeto
Chimera Agent — um framework open-source de agentes com governança e benchmarks honestos.

Recebe a MANCHETE e a DESCRIÇÃO de uma matéria. Escreva UMA a DUAS frases dizendo o que aquilo muda
para quem constrói agentes de IA. Em {lang}.

REGRAS INVIOLÁVEIS:
- NÃO afirme nenhum fato que não esteja na manchete ou na descrição. Nada de números, nomes,
  datas ou citações que não estejam ali.
- É comentário, não reportagem. Opinião e implicação, não recontagem da notícia.
- No máximo {cap} caracteres. Sem markdown, sem aspas ao redor, sem prefixo.
- PULAR é só para matéria que não trata de IA, de LLM ou de agentes. Se trata, há o que dizer:
  toda notícia da área muda alguma coisa para quem constrói — custo, risco, expectativa, mercado.

MANCHETE: {headline}
DESCRIÇÃO: {description}"""


def comment_for(item: dict, lang: str) -> str:
    key = env("OPENROUTER_API_KEY")
    if not key:
        return ""
    body = {
        "model": env("CHIMERA_DIGEST_MODEL") or "deepseek/deepseek-chat",
        "messages": [
            {
                "role": "user",
                "content": PROMPT.format(
                    lang="português do Brasil" if lang == "pt" else "English",
                    cap=COMMENT_MAX - 40,
                    headline=item["headline"],
                    description=item["description"] or "(sem descrição)",
                ),
            }
        ],
        "max_tokens": 260,
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            **UA,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": SITE,
            "X-Title": "Chimera digest",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.load(resp)
        text = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        log(f"comentário {lang}: {type(exc).__name__} {str(exc)[:70]}")
        return ""

    text = " ".join(text.replace("\n", " ").split()).strip('"').strip()
    if text.upper().startswith("PULAR") or not text:
        return ""
    return fit(text, lang)


def fit(text: str, lang: str) -> str:
    """Cabe no teto mantendo frases inteiras.

    Cortar no meio de uma frase muda o que ela diz, então isso nunca acontece aqui. Mas descartar
    o comentário inteiro porque a terceira frase não coube é jogar fora as duas que couberam — e
    na primeira rodada isso zerou o boletim: quatro itens verificados, quatro comentários longos,
    nenhuma notícia publicada. Descartar a cauda é diferente de cortar a frase.
    """
    if len(text) <= COMMENT_MAX:
        return text
    kept = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        nxt = (kept + " " + sentence).strip()
        if len(nxt) > COMMENT_MAX:
            break
        kept = nxt
    if kept:
        log(f"comentário {lang}: {len(text)} caracteres, mantidas as frases que cabem ({len(kept)})")
        return kept
    log(f"comentário {lang}: nem a primeira frase cabe em {COMMENT_MAX} — descartado")
    return ""


# --------------------------------------------------------------------------- escrita

def yaml_str(value: str) -> str:
    """Aspas duplas sempre. Uma manchete traz apóstrofo, dois-pontos e travessão; adivinhar quando
    aspas são dispensáveis é como se produz um YAML que analisa e diz outra coisa."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render(lang: str, day: str, slot: str, items: list[dict], dropped: str) -> str:
    label = {
        "pt": {"meio-dia": "meio-dia", "fim-do-dia": "fim do dia"},
        "en": {"meio-dia": "midday", "fim-do-dia": "evening"},
    }[lang][slot]
    title = f"Boletim — {day}, {label}" if lang == "pt" else f"Digest — {day}, {label}"
    # Todos os itens, não só o primeiro: o resumo é o que aparece no índice do blog e no card
    # social, e um resumo que nomeia uma das três notícias descreve o post errado.
    heads = " · ".join(i["headline"] for i in items)
    summary = (heads[:277] + "…") if len(heads) > 280 else heads
    lines = [
        "---",
        f"title: {yaml_str(title)}",
        f"date: {day}",
        "category: digest",
        f"summary: {yaml_str(summary)}",
        "items:",
    ]
    for item in items:
        lines += [
            f"  - headline: {yaml_str(item['headline'])}",
            f"    url: {item['url']}",
            f"    outlet: {yaml_str(item['outlet'])}",
            f"    published: {item['published']}",
            f"    comment: {yaml_str(item[f'comment_{lang}'])}",
        ]
    if dropped:
        lines.append(f"dropped: {yaml_str(dropped)}")
    lines += ["---", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- publicação

def gh(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    token = env("GITHUB_TOKEN_HERMES") or env("GITHUB_TOKEN")
    req = urllib.request.Request(
        "https://api.github.com" + path,
        method=method,
        headers={
            **UA,
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    if payload is not None:
        req.data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    # Nem toda resposta da API tem corpo: apagar uma ref devolve 204 vazio, e `json.load` num
    # corpo vazio levanta JSONDecodeError — que foi como a primeira publicação real morreu, DEPOIS
    # de já ter criado o branch e escrito os arquivos.
    def body_of(resp) -> dict:  # noqa: ANN001
        raw = resp.read()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return {}

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, body_of(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, body_of(exc)


CHECKS_TIMEOUT_S = int(os.environ.get("CHIMERA_DIGEST_CHECKS_TIMEOUT_S", "900"))
CHECKS_POLL_S = 20


def checks_pass(number: int, sha: str, timeout_s: int = CHECKS_TIMEOUT_S) -> bool:
    """Espera o CI do site concluir para este commit. Só devolve True se ele aprovou.

    A regra que não pode ser afrouxada: **ausência de check não é aprovação**. Logo depois de um PR
    nascer, a API responde `total_count: 0` porque o workflow ainda não registrou — tratar isso como
    "nada reprovou, então pode" é exatamente o furo que este trecho existe para fechar, e é um erro
    fácil de cometer, porque num merge manual ele se parece com pressa em vez de defeito.

    Estouro de prazo também não é aprovação. O PR fica aberto: um boletim malformado vira um PR
    esperando alguém, que é barato, em vez de uma página no ar, que não é.
    """
    limite = time.time() + timeout_s
    visto = False
    while time.time() < limite:
        st, res = gh("GET", f"/repos/{REPO}/commits/{sha}/check-runs")
        if st != 200:
            log(f"github: não consegui ler os checks de #{number} ({st})")
            time.sleep(CHECKS_POLL_S)
            continue

        runs = res.get("check_runs") or []
        if not runs:
            # Ainda não registrou. Continua esperando — ver o docstring.
            time.sleep(CHECKS_POLL_S)
            continue

        visto = True
        pendentes = [r for r in runs if r.get("status") != "completed"]
        if pendentes:
            time.sleep(CHECKS_POLL_S)
            continue

        ruins = [
            f"{r.get('name')}={r.get('conclusion')}"
            for r in runs
            if r.get("conclusion") not in ("success", "neutral", "skipped")
        ]
        if ruins:
            log(f"github: CI reprovou #{number}: {', '.join(ruins)}")
            return False
        log(f"github: CI aprovou #{number} ({len(runs)} check(s))")
        return True

    log(f"github: CI de #{number} não concluiu em {timeout_s}s (viu algum check: {visto})")
    return False


def publish(files: dict[str, str], slug: str, day: str) -> bool:
    """Branch, dois arquivos, PR, merge. Nunca commit direto na main: o rastro do PR é o que torna
    auditável um post que ninguém leu antes de publicar."""
    import base64

    status, main = gh("GET", f"/repos/{REPO}/git/ref/heads/main")
    if status != 200:
        log(f"github: não consegui ler a main ({status})")
        return False
    branch = f"blog/digest-{slug}"
    gh("POST", f"/repos/{REPO}/git/refs", {"ref": f"refs/heads/{branch}", "sha": main["object"]["sha"]})

    for path, content in files.items():
        payload = {
            "message": f"content(blog): boletim {day}",
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        # Criar exige a ausência do arquivo; atualizar exige o sha de quem está lá. Sem isto, uma
        # rodada que morreu no meio deixa o branch com metade dos arquivos e a próxima leva 422 —
        # que foi exatamente o que aconteceu na primeira publicação real.
        st_prev, prev = gh("GET", f"/repos/{REPO}/contents/{path}?ref={branch}")
        if st_prev == 200 and isinstance(prev, dict) and prev.get("sha"):
            payload["sha"] = prev["sha"]
        st, res = gh("PUT", f"/repos/{REPO}/contents/{path}", payload)
        if st not in (200, 201):
            log(f"github: falhei ao escrever {path} ({st}) {json.dumps(res)[:160]}")
            return False

    st, pr = gh(
        "POST",
        f"/repos/{REPO}/pulls",
        {
            "title": f"boletim {day}",
            "head": branch,
            "base": "main",
            "body": (
                "Boletim automático. Manchete, veículo, data e link vêm do próprio artigo; o "
                "comentário é do agente e está limitado pelo portão do site.\n\n"
                f"Arquivos: {', '.join(files)}"
            ),
        },
    )
    if st not in (200, 201):
        log(f"github: PR recusado ({st}) {json.dumps(pr)[:200]}")
        return False

    number = pr["number"]

    # O portão. O site já sabe reprovar um boletim malformado — `postProblems()` e
    # `digestProblems()` são testados no vitest dele —, mas até aqui o merge saía no mesmo segundo
    # em que o PR nascia, antes de qualquer check ter o que dizer. A validação existia e nunca
    # chegava a rodar onde importava.
    #
    # A alternativa seria reescrever essas regras em Python, aqui. Seria uma segunda cópia da regra,
    # correta exatamente uma vez: no dia em que o schema do site mudasse, este script continuaria
    # aprovando o que o site passou a recusar. Esperar o CI reusa a fonte única.
    if not checks_pass(number, pr["head"]["sha"]):
        log(f"github: PR #{number} fica ABERTO — o CI do site não aprovou")
        return False

    # O merge é o passo autônomo que o Bruno autorizou. A trava é o escopo: este job só escreve
    # sob content/blog/, então só isso pode chegar à main por aqui.
    st, res = gh("PUT", f"/repos/{REPO}/pulls/{number}/merge", {"merge_method": "squash"})
    if st != 200:
        log(f"github: merge do PR #{number} recusado ({st}) {json.dumps(res)[:200]}")
        return False
    gh("DELETE", f"/repos/{REPO}/git/refs/heads/{branch}")
    log(f"publicado: PR #{number} merjado")
    return True


# --------------------------------------------------------------------------- estado

def load_seen() -> list[str]:
    try:
        with open(SEEN_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def save_seen(urls: list[str]) -> None:
    os.makedirs(os.path.dirname(SEEN_PATH), exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as fh:
        json.dump(urls[-600:], fh, ensure_ascii=False, indent=0)


# --------------------------------------------------------------------------- principal

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="fim-do-dia", choices=["meio-dia", "fim-do-dia"])
    ap.add_argument("--max-items", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    seen = load_seen()
    seen_set = set(seen)

    candidates = from_feeds() + from_openrouter() + from_search()
    by_url: dict[str, dict] = {}
    for cand in candidates:
        if cand["url"] and cand["url"] not in seen_set:
            by_url.setdefault(cand["url"], cand)
    log(f"colheita: {len(candidates)} candidatos, {len(by_url)} inéditos")

    accepted: list[dict] = []
    reasons: list[str] = []
    used_outlets: set[str] = set()

    # Verificar primeiro, escolher depois. Na primeira versão a diversidade de veículo era uma
    # regra e rejeitou três itens da Exame numa rodada que publicou UM — preferir variedade não
    # pode custar o boletim. Agora ela ordena os aprovados; não elimina nenhum.
    # Mais recente primeiro, e verifica sob demanda.
    #
    # Com cinco feeds brasileiros a colheita dava ~30 candidatos e verificar todos era barato. Com
    # treze feeds passa de setenta, cada um uma requisição de até 25s — verificar tudo para depois
    # escolher três estoura o teto de 900s do job. A ordem por data faz a parada antecipada custar
    # os itens mais velhos, que são justamente os que não entrariam.
    ordem = sorted(
        by_url.values(),
        key=lambda c: parse_date(c.get("feed_date") or "") or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    alvo = max(args.max_items * 3, 6)
    verified: list[dict] = []
    for cand in ordem:
        if len(verified) >= alvo:
            log(f"parada antecipada: {len(verified)} verificados bastam para escolher {args.max_items}")
            break
        item, why = verify(cand)
        if not item:
            reasons.append(why)
            log(f"  recusado: {why}  {cand['url'][:90]}")
            continue
        verified.append(item)

    verified.sort(key=lambda i: i["published_at"], reverse=True)
    ordered: list[dict] = []
    for pass_no in (1, 2):  # a primeira passada pega um por veículo; a segunda completa
        for item in verified:
            if item in ordered:
                continue
            if pass_no == 1 and item["outlet"] in used_outlets:
                continue
            ordered.append(item)
            used_outlets.add(item["outlet"])

    for item in ordered:
        if len(accepted) >= args.max_items:
            break
        item["comment_pt"] = comment_for(item, "pt")
        item["comment_en"] = comment_for(item, "en")
        if not item["comment_pt"] or not item["comment_en"]:
            reasons.append("sem comentário utilizável")
            log(
                f"  sem comentário ({'pt' if not item['comment_pt'] else 'en'}): "
                f"{item['headline'][:70]} | descrição {len(item['description'])} car."
            )
            continue
        accepted.append(item)

    tally: dict[str, int] = {}
    for why in reasons:
        tally[why] = tally.get(why, 0) + 1
    # Sempre, e antes da saída antecipada. Uma rodada que não publicou nada e não disse por quê é
    # indistinguível de uma rodada que não rodou.
    if tally:
        log("motivos: " + ", ".join(f"{why} ({n})" for why, n in sorted(tally.items(), key=lambda kv: -kv[1])))

    if not accepted:
        log("nada passou nas checagens — nenhum boletim publicado neste horário")
        return 0
    # Contar o que foi EXAMINADO, não o que foi reunido.
    #
    # Com treze feeds a colheita passa de mil itens e a rodada para de verificar assim que tem o
    # bastante. Dizer "1448 candidatos, 3 publicados" faz o leitor entender que 1445 foram
    # avaliados e reprovados — foram nove. Um número honesto sobre descartes não pode ser o
    # tamanho da pilha de onde ninguém tirou nada.
    examinados = len(verified) + len(reasons)
    base = (
        f"{examinados} candidatos examinados de {len(by_url)} reunidos, {len(accepted)} publicados."
    )
    dropped = (
        base
        + " Descartados: "
        + ", ".join(f"{why} ({n})" for why, n in sorted(tally.items(), key=lambda kv: -kv[1])[:6])
    ) if tally else base

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = f"boletim-{day}-{args.slot}"
    files = {
        f"content/blog/pt/{slug}.md": render("pt", day, args.slot, accepted, dropped),
        f"content/blog/en/{slug}.md": render("en", day, args.slot, accepted, dropped),
    }

    if args.dry_run:
        for path, content in files.items():
            print(f"\n===== {path} =====\n{content}")
        print(f"\n(dry-run: nada publicado)  descartes: {dropped}")
        return 0

    if not publish(files, f"{day}-{args.slot}", day):
        return 1

    save_seen(seen + [i["url"] for i in accepted])
    log(f"boletim {slug}: {len(accepted)} itens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
