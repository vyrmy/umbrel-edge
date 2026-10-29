# syntax=docker/dockerfile:1

# A personal desktop (ARCHITECTURE.md, decision 16): linuxserver's Webtop (Ubuntu, XFCE,
# Selkies), which already ships Chromium, plus Signal Desktop and a WhatsApp Web window, all
# started with the session. WhatsApp has no Linux app; wrapped-whatsapp explains the window.
# The base tag floats on purpose. The weekly build picks up linuxserver's latest Chromium and
# Signal's latest release (each Signal build stops working about 90 days after it ships), and
# the Umbrel apps pin the result by digest.
FROM lscr.io/linuxserver/webtop:ubuntu-xfce

ENV TITLE=Desktop

RUN \
  echo "**** install signal desktop ****" && \
  apt-get update && \
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gnupg && \
  curl -fsSL https://updates.signal.org/desktop/apt/keys.asc \
    | gpg --dearmor -o /usr/share/keyrings/signal-desktop-keyring.gpg && \
  curl -fsSL -o /etc/apt/sources.list.d/signal-desktop.sources \
    https://updates.signal.org/static/desktop/apt/signal-desktop.sources && \
  apt-get update && \
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends signal-desktop && \
  echo "**** menu entry uses the wrapper, without any --no-sandbox of its own ****" && \
  sed -i \
    's#^Exec=.*#Exec=/usr/local/bin/wrapped-signal --use-tray-icon %U#' \
    /usr/share/applications/signal-desktop.desktop && \
  echo "**** cleanup ****" && \
  apt-get autoclean && \
  rm -rf \
    /config/.cache \
    /var/lib/apt/lists/* \
    /var/tmp/* \
    /tmp/*

# /etc/chromium.d/90-egress only works while Debian's launcher sources that folder. Fail the
# weekly build if it stops, instead of shipping desktops that silently bypass the egress proxy.
RUN grep -q '^for file in /etc/chromium.d/' /usr/bin/chromium-browser

COPY root/ /
