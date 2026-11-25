#!/usr/bin/env bash
# Installs the day-trader-skill pack at the reviewed commit into ~/.claude/skills.
# Used as the cloud environment's setup script, so every fresh session has the skills.
set -e
D=$(mktemp -d)
git clone -q https://github.com/Jakeschincariol/day-trader-skill.git "$D/dts"
git -C "$D/dts" checkout -q 3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd
mkdir -p ~/.claude/skills
cp -r "$D"/dts/trader-* ~/.claude/skills/
rm -rf "$D"
