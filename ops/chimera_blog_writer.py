#!/usr/bin/env python3
"""chimera_blog_writer.py — o redator do blog do chimeraagent.space.

Roda no sidecar (uid 10000, sem git, sem dependências fora da stdlib). Publica pela API de
conteúdo do GitHub: nada é clonado, o que também respeita o disco da VPS, que está em 78%.

O QUE MUDOU, E POR QUE IMPORTA
    Isto era um boletim: manchete dos outros, link e um comentário nosso de duas frases. A defesa
    contra invenção era estrutural e forte — o modelo escrevia UM campo, limitado a 400 caracteres,
    e todo o resto da página vinha de dados verificados.

    Agora o texto é nosso, inteiro. Isso é o que o Bruno pediu e é uma coisa melhor de se ler, mas
    remove aquela defesa por completo: um artigo é exatamente onde um modelo fluente inventa número,
    data e citação sem que nada trave. Então a defesa foi refeita, e é isto:

    1. O modelo NÃO ESCREVE URL. Nenhuma. Para citar uma matéria ele usa `[S1]`, `[S2]`, e o link é
       montado aqui, a partir da lista já verificada. Fonte inventada não é detectada — ela é
       INEXPRIMÍVEL, porque não existe campo onde ela caberia.
    2. As fontes continuam em dados, no frontmatter, e a página as renderiza de lá. O corpo é
       nosso; o que dá para conferir não é.
    3. Citação longa entre aspas é reprovada pelo portão do site. Pôr frase na boca de alguém que
       existe é a única invenção que faz estrago fora deste projeto.
    4. Nove idiomas ou nenhum. Uma rodada que escreve cinco e perde quatro deixa um site que parece
       deliberado, e o leitor não tem como saber que houve falha.
    5. Nada vai para a main sem o CI do site aprovar. As regras de formato vivem no `blog.ts` de
       lá, testadas no vitest de lá; aqui não há cópia delas — cópia de regra fica correta
       exatamente uma vez.

O QUE ESTE SCRIPT RECUSA A FAZER
    * Confiar na data relativa da listagem. "Há 20 horas" não é uma data; a data sai do
      `datePublished` no JSON-LD do próprio artigo, e é o campo cuja honestidade sustenta o resto.
    * Publicar a manchete que veio da busca. A manchete é a que o artigo declara em `og:title` ou
      `<title>` — um resumo de buscador é texto de terceiro sobre o texto do veículo.
    * Reproduzir corpo de matéria. A `description` entra como insumo do modelo e nunca é publicada.
    * Inventar volume. Se nada passa, nada sai. Uma página vazia com data é pior que silêncio.

Uso:
    chimera_blog_writer.py [--sources 3] [--dry-run]        um artigo a partir do noticiário
    chimera_blog_writer.py --release 0.42.0 [--dry-run]     um artigo sobre um release nosso
    chimera_blog_writer.py --release latest
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
PRODUCT_REPO = "brcampidelli/chimera-agent"
SITE = "https://chimeraagent.space"
# O mesmo caminho de estado do boletim: o que já foi lido continua lido, e a troca de formato não
# ressuscita quarenta matérias de três dias atrás na primeira rodada.
SEEN_PATH = "/opt/data/state/blog_digest_seen.json"
LOG_PATH = "/opt/data/logs/blog-writer.log"

UA = {"User-Agent": f"Mozilla/5.0 (compatible; ChimeraWriter/1.0; +{SITE})"}

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

# Os idiomas do site. A ordem é a do seletor de idioma, e `en` vem primeiro porque é a única língua
# em que o modelo escreve sem ajuda: as outras saem melhor quando ele já formou a ideia.
#
# Esta tupla decide o futuro, não o passado. Traduzir os posts existentes é trabalho de uma vez;
# esquecer daqui é dívida que recomeça no post de amanhã — e o teste que a pega
# (`blog.test.ts`, "publishes an agent-written piece in every language or in none") mora no
# repositório do site, que não vê este arquivo.
LANGS = ("en", "pt", "es", "fr", "de", "it", "pl", "zh", "ja", "ru")
LANG_NAMES = {
    "en": "English",
    "pt": "português do Brasil",
    "es": "español",
    "fr": "français",
    "de": "Deutsch",
    "it": "italiano",
    "pl": "polski",
    "zh": "简体中文",
    "ja": "日本語",
    "ru": "русский",
}

# Frases inteiras que modelos deixam escapar quando "pensam alto" na saída final. Uma delas está no
# ar agora, num boletim em inglês: "(Alternatively, if shorter is preferred: …)". O modelo estava
# oferecendo uma escolha ao operador, e o operador era um script.
ARTIFACTS = re.compile(
    # A oferta ao operador, no fim: "(Alternatively, if shorter is preferred: …)"
    r"\s*\((?:alternatively|alternativamente|or,? if|se preferir|caso prefira)[^)]*\)\s*$"
    # A cortesia de abertura: "Here's the comment:", "Aqui está o comentário:", "Comentário:".
    # O dois-pontos tem de vir em até 40 caracteres depois do marcador, senão isto comeria uma
    # frase legítima que só por acaso começa com "Claro" e tem um dois-pontos lá adiante.
    r"|^\s*(?:aqui est[áa]|segue|here(?:'s| is)|sure|claro|coment[áa]rio|comment)\b[^:\n]{0,40}:\s*",
    re.I,
)

WRITE_PROMPT = """Você é o redator do blog do Chimera Agent — um framework open-source de agentes
de IA, com governança, avaliação honesta e fusão de modelos. Quem lê constrói agentes: gente que
programa, não gente que compra.

Recebe as MATÉRIAS que a redação leu hoje: manchete, veículo e uma descrição curta de cada uma.
Escreva UM artigo nosso a partir delas. Não é resumo das matérias, não é boletim, não é lista.
É um texto com uma TESE — o que aquilo muda para quem constrói agentes — e a tese é sua.

**ESCREVA O ARTIGO EM INGLÊS.** Estas instruções estão em português, o texto que você produz não
está: o inglês é a língua-fonte do site, e é dele que saem todas as outras traduções e o endereço
da página. Título, resumo e corpo, todos em inglês.

FORMA:
- Entre 450 e 700 palavras. Markdown. Dois ou três subtítulos `##`. Sem título `#` no corpo (o
  título vem no campo `title`).
- Comece pela ideia, não pela notícia. A primeira frase não é "a OpenAI anunciou": é o que isso
  significa. A notícia entra quando for necessária para sustentar o argumento.
- Frases diretas. Sem adjetivo de folheto ("revolucionário", "poderoso", "game-changer"), sem
  pergunta retórica de abertura, sem "no mundo acelerado da IA".
- Termine com o que fica de prático para quem constrói. Concreto, não exortação.

REGRAS INVIOLÁVEIS — o texto sai no ar sem ninguém ler antes:
- NÃO afirme nenhum fato que não esteja nas matérias. Nada de números, datas, nomes de produto,
  preços, percentuais ou resultados de benchmark que não estejam ali. O que você acrescenta é
  raciocínio, nunca informação.
- NÃO invente citação. Nenhuma frase entre aspas atribuída a alguém. Nem uma.
- NÃO escreva URL nenhuma. Para citar uma matéria, use o marcador {marcadores} exatamente assim,
  entre colchetes, no meio da frase. O link é montado depois, por um script.
- Se as matérias não sustentam uma tese honesta, responda {{"skip": true}}. Um texto vazio publicado
  é pior que uma rodada sem publicação.

Responda APENAS com um objeto JSON, sem cercas de código:
{{"title": "...", "summary": "...", "body": "..."}}

- `title`: o título do artigo. Até 70 caracteres, específico, sem dois-pontos decorativo.
- `summary`: uma frase, até 200 caracteres, dizendo a tese. É o que aparece no índice do blog.
- `body`: o artigo em markdown, com os marcadores de fonte.

MATÉRIAS:
{material}"""

TRANSLATE_PROMPT = """Traduza para {idioma} o artigo abaixo, do blog do Chimera Agent — um
framework open-source de agentes de IA. Quem lê programa.

REGRAS:
- Traduza o SENTIDO, não as palavras. Escreva como um engenheiro escreveria esse mesmo argumento
  na sua língua. Não é para soar traduzido.
- Preserve a estrutura exata: mesma quantidade de parágrafos, mesmos subtítulos `##` na mesma
  ordem, mesma ênfase.
- Os marcadores de fonte — {marcadores} — ficam IDÊNTICOS, entre colchetes, na posição que a
  frase da sua língua exigir. Eles viram links depois; um marcador perdido é uma fonte perdida.
- Não traduza: Chimera, nomes de empresa e de produto, termos de código entre crases.
- Não resuma, não corte, não acrescente. O texto na sua língua diz o mesmo que o original.

Responda APENAS com um objeto JSON, sem cercas de código:
{{"title": "...", "summary": "...", "body": "..."}}

ARTIGO:
{artigo}"""

UPDATE_PROMPT = """Você é o redator do blog do Chimera Agent — um framework open-source de agentes
de IA. Saiu a versão {version}, e você escreve o texto que a anuncia. Quem lê constrói agentes.

Recebe as NOTAS DE RELEASE, escritas por quem programou. Escreva UM artigo nosso sobre elas.

**ESCREVA O ARTIGO EM INGLÊS**, pelo mesmo motivo: o inglês é a língua-fonte do site.

FORMA:
- Entre 350 e 600 palavras. Markdown. Dois ou três subtítulos `##`. Sem título `#` no corpo.
- Não repita a lista. As notas já existem e estão linkadas. Escolha o que MUDA para quem usa e
  explique por que aquilo estava errado antes.
- Frases diretas. Sem adjetivo de folheto. Sem "estamos empolgados em anunciar".
- Termine com o que a pessoa faz agora: o comando, o passo, a página.

REGRAS INVIOLÁVEIS — o texto sai no ar sem ninguém ler antes:
- NÃO afirme nada que não esteja nas notas. Nada de números de desempenho, datas, planos futuros
  ou capacidades que não estejam ali. Se as notas não dizem, nós não dizemos.
- NÃO invente citação.
- NÃO escreva URL nenhuma, exceto o marcador {marcador} — que vira o link das notas de release.

Responda APENAS com um objeto JSON, sem cercas de código:
{{"title": "...", "summary": "...", "body": "..."}}

- `title`: até 70 caracteres. Pode nomear a versão.
- `summary`: uma frase, até 200 caracteres, dizendo o que mudou.

NOTAS DE RELEASE DA VERSÃO {version}:
{notas}"""


def _clean(text: str) -> str:
    """Uma linha, sem aspas de embrulho e sem os restos que o modelo dirige ao operador."""
    text = " ".join(str(text).replace("\n", " ").split())
    text = ARTIFACTS.sub("", text).strip()
    return text.strip('"').strip("'").strip()


def _ask(prompt: str, max_tokens: int) -> dict | None:
    """Uma chamada ao modelo que devolve JSON, ou None. Nunca levanta."""
    key = env("OPENROUTER_API_KEY")
    if not key:
        return None
    body = {
        "model": env("CHIMERA_WRITER_MODEL") or "deepseek/deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            **UA,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": SITE,
            "X-Title": "Chimera writer",
        },
    )
    # Espera crescente no 429, e só no 429.
    #
    # A rodada faz uma chamada por idioma, seguidas — uma para escrever e as outras para traduzir —,
    # e o limite de taxa aparece lá pela sétima. Foi assim que uma execução real morreu no chinês
    # depois de sete idiomas prontos: a retentativa disparou um segundo depois, que é exatamente o
    # intervalo em que um 429 continua sendo 429. Repetir sem esperar não é uma tentativa, é a mesma
    # chamada. Cada idioma novo empurra a rodada mais fundo nessa faixa, e não afrouxa a espera.
    for espera in (0, 20, 45, 90):
        if espera:
            log(f"modelo: limite de taxa, esperando {espera}s")
            time.sleep(espera)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            raw = data["choices"][0]["message"]["content"].strip()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                continue
            log(f"modelo: HTTP {exc.code}")
            return None
        except Exception as exc:  # noqa: BLE001
            log(f"modelo: {type(exc).__name__} {str(exc)[:70]}")
            return None
    else:
        log("modelo: limite de taxa persistiu depois de três esperas")
        return None
    # Alguns modelos ignoram response_format e devolvem a cerca de código mesmo assim.
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log(f"modelo: resposta não é JSON ({raw[:70]})")
        return None
    return parsed if isinstance(parsed, dict) else None


MARKER = re.compile(r"\[S(\d+)\]")
BARE_URL = re.compile(r"https?://")


def markers_for(n: int) -> str:
    return ", ".join(f"[S{i + 1}]" for i in range(n))


def shape_problems(art: dict, n_sources: int, title_max: int = 90) -> list[str]:
    """O que está errado com o que o modelo devolveu, antes de virar arquivo.

    Isto NÃO é uma segunda cópia das regras do site. As regras do site são sobre o formato de um
    post e vivem no `blog.ts`, testadas no vitest dele; duplicá-las aqui produziria uma cópia
    correta exatamente uma vez, até o dia em que o schema mudasse. O que se checa aqui é outra
    coisa: se a RODADA devolveu o que se pediu. Campo faltando, marcador para uma fonte que não
    existe, URL escrita à mão onde o combinado era um marcador.

    A URL crua é a que importa. O modelo não escreve link nenhum — quem monta o link é
    `link_sources`, a partir da lista verificada. Uma fonte inventada não é detectada aqui: ela é
    inexprimível, porque o campo onde ela caberia não existe. Este teste só pega a rodada que
    ignorou a instrução.
    """
    problems = []
    for field in ("title", "summary", "body"):
        if not str(art.get(field, "")).strip():
            problems.append(f"campo {field} vazio")
    if problems:
        return problems

    if BARE_URL.search(art["body"]):
        problems.append("o corpo traz URL escrita pelo modelo — o combinado é marcador")
    usados = {int(m) for m in MARKER.findall(art["body"])}
    fora = sorted(i for i in usados if not 1 <= i <= n_sources)
    if fora:
        problems.append(f"marcadores fora da lista de fontes: {fora}")
    if n_sources and not usados:
        problems.append("o corpo não cita nenhuma fonte")
    if len(art["title"]) > title_max:
        problems.append(f"título com {len(art['title'])} caracteres, teto {title_max}")
    if len(art["summary"]) > 240:
        problems.append(f"resumo com {len(art['summary'])} caracteres")
    return problems


# Palavras funcionais que aparecem em qualquer parágrafo de português ou espanhol e praticamente
# nunca em prosa inglesa. Não é detecção de idioma: é a pergunta "isto está em inglês?", que é a
# única que interessa aqui.
NAO_INGLES = re.compile(
    r"\b(não|nao|são|sao|está|esta[oó]|para|com|uma|dos|das|pelo|pela|mais|também|"
    r"que|porque|quando|sobre|entre|muito|tem|foi|ser)\b",
    re.I,
)

DIGITOS = re.compile(r"\d[\d.,]*")


def looks_english(text: str) -> bool:
    """Se o texto está em inglês. Instrução no prompt não é trava: esta é a trava.

    O prompt está em português e a primeira execução real devolveu o artigo inteiro em português —
    título, resumo e corpo — para o arquivo `blog/en/`, com o endereço da página em português e as
    demais traduções saindo de um "original" que não era o idioma-fonte. Nada quebrou; ficou só
    errado, que é o modo de falha caro.
    """
    achadas = {m.group(0).lower() for m in NAO_INGLES.finditer(text)}
    return len(achadas) < 3


def invented_numbers(body: str, material: str) -> list[str]:
    """Números do corpo que não aparecem no material.

    Um número é a afirmação factual mais densa que um texto carrega e a mais fácil de conferir. O
    resto do que um modelo inventa — um nome, uma ênfase — não dá para checar assim, e não se
    finge que dá: isto pega uma classe de invenção, não a invenção.

    Compara sem separador, porque `3.6` vira `3,6` e `1,500` vira `1.500` conforme quem escreve.
    """
    limpo = lambda s: s.replace(".", "").replace(",", "").rstrip("0") or "0"  # noqa: E731
    no_material = {limpo(m.group(0)) for m in DIGITOS.finditer(material)}
    fora = []
    for m in DIGITOS.finditer(body):
        bruto = m.group(0).rstrip(".,")
        if len(bruto.strip(".,")) >= 2 and limpo(bruto) not in no_material:
            fora.append(bruto)
    return fora


def write_article(items: list[dict]) -> dict | None:
    """O artigo em inglês, de uma chamada, a partir das matérias verificadas.

    Inglês primeiro e traduções depois, e não os nove numa tacada como fazia o boletim: dois
    parágrafos cabiam numa resposta só, um artigo de seiscentas palavras vezes nove não cabe — o
    modelo trunca e o truncamento não avisa. E há um ganho junto: quem traduz vê o argumento
    pronto, então as nove versões dizem a mesma coisa em vez de serem nove opiniões paralelas.
    """
    material = "\n\n".join(
        f"[S{i + 1}] {it['headline']}\n     veículo: {it['outlet']}  ·  data: {it['published']}\n"
        f"     descrição: {it['description'] or '(sem descrição)'}"
        for i, it in enumerate(items)
    )
    for _ in (1, 2):
        art = _ask(
            WRITE_PROMPT.format(marcadores=markers_for(len(items)), material=material),
            max_tokens=3000,
        )
        if art is None:
            continue
        if art.get("skip"):
            log("redação: o modelo não viu tese sustentável nas matérias de hoje")
            return None
        problems = shape_problems(art, len(items))
        if not looks_english(f"{art.get('title', '')} {art.get('body', '')}"):
            problems.append("o artigo-fonte não saiu em inglês")
        fora = invented_numbers(str(art.get("body", "")), material)
        if fora:
            problems.append(f"números que não estão no material: {fora[:5]}")
        if problems:
            log("redação: " + "; ".join(problems))
            continue
        return {k: art[k] for k in ("title", "summary", "body")}
    return None


def write_update(version: str, notes: str) -> dict | None:
    """O artigo sobre um release nosso. Uma fonte só: as notas, que são nossas.

    As mesmas travas do artigo de fora valem aqui. As notas serem nossas reduz o risco de inventar
    sobre terceiros, não o de inventar: um número de desempenho que ninguém mediu é pior vindo de
    nós, porque quem lê tem motivo para acreditar.
    """
    material = notes[:12000]
    for _ in (1, 2):
        art = _ask(
            UPDATE_PROMPT.format(marcador="[S1]", version=version, notas=material),
            max_tokens=2600,
        )
        if art is None:
            continue
        if art.get("skip"):
            log("redação: o modelo não escreveu o texto do release")
            return None
        problems = shape_problems(art, 1)
        if not looks_english(f"{art.get('title', '')} {art.get('body', '')}"):
            problems.append("o artigo-fonte não saiu em inglês")
        fora = invented_numbers(str(art.get("body", "")), f"{material} {version}")
        if fora:
            problems.append(f"números que não estão nas notas: {fora[:5]}")
        if problems:
            log("redação: " + "; ".join(problems))
            continue
        return {k: art[k] for k in ("title", "summary", "body")}
    return None


def _translate_once(art: dict, lang: str, n_sources: int) -> dict | None:
    origem = json.dumps(art, ensure_ascii=False, indent=2)
    out = _ask(
        TRANSLATE_PROMPT.format(
            idioma=LANG_NAMES[lang], marcadores=markers_for(n_sources), artigo=origem
        ),
        max_tokens=5000,
    )
    if out is None:
        return None
    # O teto de título de uma TRADUÇÃO é relativo ao original, não absoluto.
    #
    # O teto fixo de 90 reprovou o alemão com 92, e o alemão estava certo: a mesma frase corre uns
    # 30% mais longa. Uma regra calibrada no inglês que reprova o alemão por ser alemão é a terceira
    # desta leva — junto com um regex que via "arnês" dentro de "harness" e um "veio igual ao
    # inglês" que acusava cognatos. O que se quer barrar aqui é título que virou parágrafo, e isso
    # se mede contra o original.
    problems = shape_problems(out, n_sources, title_max=max(90, int(len(art["title"]) * 1.8)))
    if problems:
        log(f"tradução {lang}: " + "; ".join(problems))
        return None
    # Os marcadores que sobreviveram têm de ser os mesmos. Um perdido é uma fonte que some do
    # texto naquele idioma — e some em silêncio, porque a página continua bonita sem ela.
    if set(MARKER.findall(out["body"])) != set(MARKER.findall(art["body"])):
        log(f"tradução {lang}: os marcadores de fonte não bateram com o original")
        return None
    if out["body"].strip() == art["body"].strip():
        # Com prévia: sem ela, a única forma de diagnosticar isto é reproduzir a rodada inteira à
        # mão, que foi o que custou uma hora na primeira vez.
        log(f"tradução {lang}: veio o texto em inglês de volta — {out['body'][:90]!r}")
        return None
    return {k: out[k] for k in ("title", "summary", "body")}


def translate(art: dict, lang: str, n_sources: int) -> dict | None:
    """O artigo num idioma, ou None. Uma chamada por idioma, com uma segunda tentativa.

    A retentativa não é otimismo: como a rodada é todos-ou-nenhum, uma única chamada instável custa
    o texto do dia inteiro, em todos os idiomas. Foi o que aconteceu na primeira execução real — o
    modelo devolveu o inglês para o português e a rodada morreu ali; a mesma chamada, repetida,
    traduziu sem problema. Uma tentativa a mais é barata, e "todos ou nenhum" continua de pé.
    """
    if lang == "en":
        return art
    for attempt in (1, 2):
        out = _translate_once(art, lang, n_sources)
        if out is not None:
            if attempt == 2:
                log(f"tradução {lang}: saiu na segunda tentativa")
            return out
    return None


def link_sources(body: str, items: list[dict], numbered: bool = True) -> str:
    """Troca cada marcador pelo link da fonte.

    Numerado, e não com a manchete como texto do link. A primeira versão inseria a manchete inteira,
    porque ela é o título que a pessoa vai encontrar ao clicar — e traduzi-la seria entregar um
    título que não existe na página de destino. O raciocínio continua certo; o efeito era ilegível:
    uma manchete inglesa de treze palavras no meio de uma frase em português, a cada tradução.
    Uma coluna cita por número e põe as manchetes na lista do fim, que é de onde a página as
    renderiza de qualquer jeito, na língua do veículo.

    `numbered=False` para o texto de release, que não tem bloco de fontes embaixo: ali um `[1]`
    apontaria para uma lista que a página não mostra.
    """
    def troca(m: re.Match) -> str:
        i = int(m.group(1))
        item = items[i - 1]
        texto = f"[{i}]" if numbered else item["headline"]
        return f"[{texto}]({item['url']})"

    return MARKER.sub(troca, body)


# --------------------------------------------------------------------------- escrita

def yaml_str(value: str) -> str:
    """Aspas duplas sempre. Uma manchete traz apóstrofo, dois-pontos e travessão; adivinhar quando
    aspas são dispensáveis é como se produz um YAML que analisa e diz outra coisa."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render(art: dict, day: str, category: str, items: list[dict], dropped: str, version: str) -> str:
    """Um arquivo markdown, com o frontmatter que o portão do site sabe reprovar.

    As fontes vão para o frontmatter e a página as renderiza de lá — a única parte do formato de
    boletim que valia a pena manter. O corpo é nosso, e é aí que mora o risco novo; a defesa dele
    está no `blog.ts` do site, e o merge só acontece depois que o CI de lá aprova.
    """
    lines = [
        "---",
        f"title: {yaml_str(art['title'])}",
        f"date: {day}",
        f"category: {category}",
        f"summary: {yaml_str(art['summary'])}",
    ]
    if category == "update":
        lines.append(f"version: {yaml_str(version)}")
    # Só o texto sobre os outros carrega bloco de fontes. Num texto sobre um release nosso, a
    # "fonte" é a nossa própria nota — ela vira o link no corpo e não vira uma lista que finge
    # apuração externa.
    if category == "analysis" and items:
        lines.append("sources:")
        for item in items:
            lines += [
                f"  - headline: {yaml_str(item['headline'])}",
                f"    url: {item['url']}",
                f"    outlet: {yaml_str(item['outlet'])}",
                f"    published: {item['published']}",
            ]
    if dropped:
        lines.append(f"dropped: {yaml_str(dropped)}")
    lines += ["---", "", link_sources(art["body"].strip(), items, numbered=category == "analysis"), ""]
    return "\n".join(lines)


def compose(art_en: dict, items: list[dict], day: str, slug: str, category: str, dropped: str, version: str) -> dict[str, str] | None:
    """Os nove arquivos, ou None.

    Nove ou nenhum, e o portão do site também exige isso. Uma rodada que escreve cinco idiomas e
    perde quatro deixa um site que parece deliberado: quatro línguas cujo blog simplesmente tem
    menos coisa. O leitor não tem como saber que houve uma falha, então quem sabe é este `return`.
    """
    files: dict[str, str] = {}
    for lang in LANGS:
        art = translate(art_en, lang, len(items)) if lang != "en" else art_en
        if art is None:
            log(f"composição: {lang} não saiu — nada será publicado nesta rodada")
            return None
        files[f"content/blog/{lang}/{slug}.md"] = render(art, day, category, items, dropped, version)
    return files


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


def publish(files: dict[str, str], slug: str, day: str, category: str) -> bool:
    """Branch, nove arquivos, PR, merge. Nunca commit direto na main: o rastro do PR é o que torna
    auditável um post que ninguém leu antes de publicar."""
    import base64

    status, main = gh("GET", f"/repos/{REPO}/git/ref/heads/main")
    if status != 200:
        log(f"github: não consegui ler a main ({status})")
        return False
    branch = f"blog/{category}-{slug}"
    gh("POST", f"/repos/{REPO}/git/refs", {"ref": f"refs/heads/{branch}", "sha": main["object"]["sha"]})

    for path, content in files.items():
        payload = {
            "message": f"content(blog): {category} {slug}",
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
            "title": f"blog: {slug}",
            "head": branch,
            "base": "main",
            "body": (
                "Texto nosso, escrito por agente, em todos os idiomas do site.\n\n"
                "O modelo não escreve URL nenhuma: cita as fontes por marcador e os links são "
                "montados a partir da lista verificada, então uma fonte inventada não é detectada "
                "— ela é inexprimível. O resto das defesas está no portão deste repositório, e o "
                "merge só sai se o CI aprovar.\n\n"
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

def release_notes(version: str) -> tuple[str, str] | None:
    """(versão, notas) do release do produto, ou None. `latest` resolve para a última publicada."""
    path = (
        f"/repos/{PRODUCT_REPO}/releases/latest"
        if version == "latest"
        else f"/repos/{PRODUCT_REPO}/releases/tags/v{version}"
    )
    st, res = gh("GET", path)
    if st != 200 or not isinstance(res, dict):
        log(f"github: não achei o release {version} ({st})")
        return None
    tag = str(res.get("tag_name") or "").lstrip("v")
    body = str(res.get("body") or "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", tag):
        log(f"github: tag {tag!r} não é uma versão que o portão do site aceite")
        return None
    if len(body) < 80:
        log(f"github: as notas de {tag} têm {len(body)} caracteres — não dá para escrever sobre isso")
        return None
    return tag, body


def free_slug(base: str) -> str:
    """Um slug que ainda não existe na main.

    Sem data no nome, que é como os posts escritos à mão são nomeados aqui. O preço é a colisão —
    dois textos sobre o mesmo assunto na mesma semana —, e o preço é pago aqui em vez de virar um
    arquivo sobrescrito, que é a forma silenciosa de perder um post publicado.
    """
    slug = base[:70].strip("-")
    for suffix in ("", "-2", "-3", "-4"):
        candidate = f"{slug}{suffix}"
        st, _ = gh("GET", f"/repos/{REPO}/contents/content/blog/en/{candidate}.md")
        if st == 404:
            return candidate
    return f"{slug}-{datetime.now(UTC):%Y-%m-%d}"


def run_update(args) -> int:
    found = release_notes(args.release)
    if not found:
        return 1
    version, notes = found
    art = write_update(version, notes)
    if not art:
        return 1

    tag_url = f"https://github.com/{PRODUCT_REPO}/releases/tag/v{version}"
    # Uma "fonte" que é nossa: as próprias notas. Serve para o marcador [S1] virar link; não vai
    # para o frontmatter, porque a página só mostra bloco de fontes num texto sobre os outros.
    notes_item = {
        "headline": f"Chimera Agent v{version}",
        "url": tag_url,
        "outlet": "GitHub",
        "published": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = slugify(art["title"]) or f"chimera-{version.replace('.', '-')}"

    if args.dry_run:
        files = compose(art, [notes_item], day, slug, "update", "", version)
        return dump(files, "")

    slug = free_slug(slug)
    files = compose(art, [notes_item], day, slug, "update", "", version)
    if not files:
        return 1
    if not publish(files, slug, f"v{version}", "update"):
        return 1
    log(f"release v{version}: publicado como {slug}")
    return 0


def dump(files: dict[str, str] | None, dropped: str) -> int:
    if not files:
        print("(dry-run: a rodada não produziu os nove arquivos)")
        return 1
    for path, content in files.items():
        print(f"\n===== {path} =====\n{content}")
    print(f"\n(dry-run: nada publicado)  descartes: {dropped}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sources", type=int, default=3, help="quantas matérias alimentam o artigo")
    ap.add_argument("--release", help="escreve sobre um release do produto: 0.42.0 ou 'latest'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.release:
        return run_update(args)

    seen = load_seen()
    seen_set = set(seen)

    candidates = from_feeds() + from_openrouter() + from_search()
    by_url: dict[str, dict] = {}
    for cand in candidates:
        if cand["url"] and cand["url"] not in seen_set:
            by_url.setdefault(cand["url"], cand)
    log(f"colheita: {len(candidates)} candidatos, {len(by_url)} inéditos")

    reasons: list[str] = []

    # Verificar em ordem de data e parar assim que houver material suficiente. Com treze feeds a
    # colheita passa de setecentos itens; verificar todos para depois escolher três estoura o teto
    # de tempo do job, e os que ficam de fora são justamente os mais velhos.
    ordem = sorted(
        by_url.values(),
        key=lambda c: parse_date(c.get("feed_date") or "") or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    alvo = max(args.sources * 3, 6)
    verified: list[dict] = []
    for cand in ordem:
        if len(verified) >= alvo:
            log(f"parada antecipada: {len(verified)} verificados bastam para escolher {args.sources}")
            break
        item, why = verify(cand)
        if not item:
            reasons.append(why)
            log(f"  recusado: {why}  {cand['url'][:90]}")
            continue
        verified.append(item)

    verified.sort(key=lambda i: i["published_at"], reverse=True)
    used_outlets: set[str] = set()
    ordered: list[dict] = []
    for pass_no in (1, 2):  # a primeira passada pega um por veículo; a segunda completa
        for item in verified:
            if item in ordered:
                continue
            if pass_no == 1 and item["outlet"] in used_outlets:
                continue
            ordered.append(item)
            used_outlets.add(item["outlet"])
    items = ordered[: args.sources]

    tally: dict[str, int] = {}
    for why in reasons:
        tally[why] = tally.get(why, 0) + 1
    # Sempre, e antes de qualquer saída antecipada. Uma rodada que não publicou nada e não disse
    # por quê é indistinguível de uma rodada que não rodou.
    if tally:
        log("motivos: " + ", ".join(f"{why} ({n})" for why, n in sorted(tally.items(), key=lambda kv: -kv[1])))

    if not items:
        log("nenhuma matéria passou nas checagens — nada escrito hoje")
        return 0

    # Contar o que foi EXAMINADO, não o que foi reunido. Dizer "1448 candidatos, 1 publicado" faz
    # o leitor entender que 1447 foram avaliados e reprovados; foram nove.
    examinados = len(verified) + len(reasons)
    dropped = f"{examinados} matérias examinadas de {len(by_url)} reunidas, {len(items)} lidas para este texto."
    if tally:
        dropped += " Descartadas: " + ", ".join(
            f"{why} ({n})" for why, n in sorted(tally.items(), key=lambda kv: -kv[1])[:6]
        )

    art = write_article(items)
    if not art:
        return 0

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = slugify(art["title"])
    if not slug:
        log("redação: o título não produziu slug utilizável")
        return 1

    if args.dry_run:
        return dump(compose(art, items, day, slug, "analysis", dropped, ""), dropped)

    slug = free_slug(slug)
    files = compose(art, items, day, slug, "analysis", dropped, "")
    if not files:
        return 1
    if not publish(files, slug, day, "analysis"):
        return 1

    save_seen(seen + [i["url"] for i in items])
    log(f"artigo {slug}: escrito a partir de {len(items)} matérias, nos {len(LANGS)} idiomas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
