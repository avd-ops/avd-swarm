# avd-buddy Container App image (docs/decisions/0010-avd-buddy-container-app-hosting.md, docs/buddy-app.md).
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.

FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir pyyaml==6.0.2

COPY assets/buddy/ /app/assets/buddy/
COPY governance/buddy-voice.yml /app/governance/buddy-voice.yml
COPY scripts/voice_check.py /app/scripts/voice_check.py
COPY scripts/build_buddy_icons.py /app/scripts/build_buddy_icons.py
COPY buddy/ /app/buddy/

RUN python3 /app/scripts/build_buddy_icons.py && \
    useradd --create-home --uid 10001 buddy && \
    chown -R buddy:buddy /app

USER 10001
ENV PORT=8080 \
    BUDDY_REQUIRE_EASY_AUTH=true \
    BUDDY_SIM_MODE=false

EXPOSE 8080
CMD ["python3", "/app/buddy/server.py"]
