import re
import unicodedata

def slugify(text: str) -> str:
    # Replace the German sharp s with 'ss'
    text = text.replace('\u00df', 'ss')
    # Unicode normalize with NFKD and drop combining marks
    text = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('ASCII')
    # Lowercase the text
    text = text.lower()
    # Drop every character that is not [a-z0-9] by turning each RUN of such characters into a single '-'
    text = re.sub(r'[^a-z0-9]+', '-', text)
    # Strip any leading and trailing '-'
    text = text.strip('-')
    # Return empty string if the text has nothing slug-worthy
    return text if text else ''