#!/usr/bin/env bash
# Backend-only build (used for combined-host deploys, e.g. Render).
# Not needed on Hostinger if you only push backend/ there and build the
# frontend separately for Vercel.
set -o errexit

pip install -r requirements.txt
