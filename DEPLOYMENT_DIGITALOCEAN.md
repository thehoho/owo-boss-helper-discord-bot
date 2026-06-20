# DigitalOcean Deployment Guide

This deployment keeps the bot online continuously, preserves the SQLite databases and JSON state files, and restarts the process automatically after crashes or server reboots.

## Recommended server

- Provider: DigitalOcean
- Product: Basic Droplet
- Image: Ubuntu 24.04 LTS
- Size: 1 GiB RAM / 1 vCPU / 25 GiB SSD
- Region: London or Frankfurt
- Authentication: SSH key
- Backups: weekly at minimum
- Monitoring: enabled

The bot does not host a website, so it needs no inbound HTTP or HTTPS ports. SSH is the only inbound port required.

## 1. Create an SSH key on Windows

Open PowerShell:

```powershell
ssh-keygen -t ed25519 -C "owo-boss-helper"
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

Copy the public key and add it when creating the Droplet. Never share the private key.

## 2. Create the Droplet

In DigitalOcean:

1. Create a new project.
2. Create a Droplet.
3. Select Ubuntu 24.04 LTS.
4. Select the Basic 1 GiB plan.
5. Choose London or Frankfurt.
6. Add your SSH public key.
7. Enable monitoring.
8. Enable weekly or daily backups.
9. Use the hostname `owo-boss-helper`.

## 3. Secure the server

Connect as root:

```bash
ssh root@YOUR_SERVER_IP
```

Run:

```bash
apt update && apt upgrade -y
apt install -y git python3 python3-venv python3-pip sqlite3 ufw rsync
adduser owohelper
usermod -aG sudo owohelper
rsync --archive --chown=owohelper:owohelper ~/.ssh /home/owohelper
ufw allow OpenSSH
ufw --force enable
```

Exit and reconnect:

```bash
exit
ssh owohelper@YOUR_SERVER_IP
```

## 4. Install the bot

```bash
sudo mkdir -p /opt/owo-boss-helper
sudo chown owohelper:owohelper /opt/owo-boss-helper
git clone https://github.com/thehoho/owo-boss-helper-discord-bot.git /opt/owo-boss-helper
cd /opt/owo-boss-helper
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
```

Set at least:

```env
DISCORD_TOKEN=your_real_bot_token
BOT_OWNER_ID=your_discord_user_id
BOT_DEVELOPER_NAME=Hassaan
BOT_GITHUB_URL=https://github.com/thehoho/owo-boss-helper-discord-bot
BOT_SUPPORT_URL=
```

Protect the secret file:

```bash
chmod 600 /opt/owo-boss-helper/.env
```

## 5. Transfer existing runtime data

From Windows PowerShell, copy the files that preserve the current servers, templates, and ticket lists:

```powershell
scp "D:\owo-boss-helper-discord-bot-github-ready\owo-boss-helper-discord-botoss_cooldown_config.json" owohelper@YOUR_SERVER_IP:/opt/owo-boss-helper/
scp "D:\owo-boss-helper-discord-bot-github-ready\owo-boss-helper-discord-bot	eam_templates.db" owohelper@YOUR_SERVER_IP:/opt/owo-boss-helper/
scp "D:\owo-boss-helper-discord-bot-github-ready\owo-boss-helper-discord-botoss_tickets.db" owohelper@YOUR_SERVER_IP:/opt/owo-boss-helper/
```

`bot_stats.db` is created automatically. Copy it too during future migrations if you want to preserve historical server and usage statistics.

## 6. Test manually

```bash
cd /opt/owo-boss-helper
.venv/bin/python bot.py
```

Confirm the bot becomes online and the console shows all cogs loaded. Stop the manual test with `Ctrl+C`.

## 7. Install the systemd service

```bash
sudo cp /opt/owo-boss-helper/deploy/owo-boss-helper.service /etc/systemd/system/owo-boss-helper.service
sudo systemctl daemon-reload
sudo systemctl enable --now owo-boss-helper
sudo systemctl status owo-boss-helper --no-pager
```

Follow live logs:

```bash
sudo journalctl -u owo-boss-helper -f
```

The bot now starts after server reboots and restarts automatically if the process exits.

## 8. Install local application backups

```bash
sudo cp /opt/owo-boss-helper/deploy/backup.sh /usr/local/bin/owo-boss-helper-backup
sudo chmod 750 /usr/local/bin/owo-boss-helper-backup
sudo /usr/local/bin/owo-boss-helper-backup
sudo crontab -e
```

Add this daily schedule:

```cron
15 4 * * * /usr/local/bin/owo-boss-helper-backup >> /var/log/owo-boss-helper-backup.log 2>&1
```

Keep DigitalOcean backups enabled as the off-server recovery layer.

## 9. Normal update workflow

```bash
cd /opt/owo-boss-helper
git pull --ff-only
.venv/bin/pip install --upgrade -r requirements.txt
sudo systemctl restart owo-boss-helper
sudo systemctl status owo-boss-helper --no-pager
```

Your `.env`, databases, JSON state, and logs are ignored by Git and remain in place.

## 10. Useful operational commands

```bash
sudo systemctl status owo-boss-helper
sudo systemctl restart owo-boss-helper
sudo journalctl -u owo-boss-helper -n 200 --no-pager
df -h
free -h
```

Inside Discord, the configured developer can use:

```text
/bot-stats
/bot-servers
```

These commands are private and owner-only.
