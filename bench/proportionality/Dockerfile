FROM python:3.12.8-slim-bookworm
COPY wheels/ /wheels/
RUN python -m pip install --no-index --find-links=/wheels \
    pytest==8.3.4 iniconfig==2.0.0 packaging==24.2 pluggy==1.5.0 \
    && rm -rf /wheels
