# The Complete Guide to Remote Control from Your Phone

<p align="center">

[![Platform](https://img.shields.io/badge/platform-iOS%20%7C%20Android-blue)](.)
[![Protocol](https://img.shields.io/badge/protocol-SSH%20%7C%20RDP%20%7C%20VNC-orange)](.)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Manage servers, computers, and devices from your phone, anytime, anywhere**

[SSH](#1-ssh-remote-control) | [Remote Desktop](#2-remote-desktop) | [Phone-to-Phone](#3-phone-to-phone-control) | [Security](#4-security-configuration) | [App Comparison](#5-app-comparison)

</p>

---

## Table of Contents

- [Introduction](#introduction)
- [1. SSH Remote Control](#1-ssh-remote-control)
- [2. Remote Desktop](#2-remote-desktop)
- [3. Phone-to-Phone Control](#3-phone-to-phone-control)
- [4. Command-Line Approach](#4-command-line-approach)
- [5. Security Configuration](#5-security-configuration)
- [6. App Comparison & Recommendations](#6-app-comparison--recommendations)
- [7. Real-World Examples](#7-real-world-examples)
- [8. FAQ](#8-faq)
- [9. Advanced Tips](#9-advanced-tips)

---

## Introduction

In the mobile era, your phone is more than a communication tool — it is a powerful remote management terminal. Whether you are:

- 🖥️ **System administrator** — needs to respond to server alerts 24/7
- 💻 **Developer** — needs to urgently fix a production bug
- 🏠 **Home user** — needs to help parents with their computer remotely
- 📱 **Enthusiast** — wants a full Linux environment in your pocket

This guide will help you find the most suitable phone-based remote control solution.

---

## 1. SSH Remote Control

SSH (Secure Shell) is the standard, secure protocol for managing Linux/Unix servers.

### 📱 Recommended Apps

#### iOS

| App | Price | Rating | Highlights |
|-----|-------|--------|-----------|
| **Termius** | Free / Subscription | ⭐⭐⭐⭐⭐ | Cross-platform sync, beautiful UI, SFTP support |
| **Blink Shell** | Paid | ⭐⭐⭐⭐⭐ | Professional grade, Mosh support, developers' first choice |
| **ShellFish** | Free / Subscription | ⭐⭐⭐⭐ | Focused on file management, integrated SFTP |
| **SecureCRT** | Paid | ⭐⭐⭐⭐ | Enterprise grade, powerful features |

**Recommendation:** Termius (free is enough) or Blink Shell (professional first choice)

#### Android

| App | Price | Rating | Highlights |
|-----|-------|--------|-----------|
| **Termius** | Free / Subscription | ⭐⭐⭐⭐⭐ | Cross-platform, full-featured |
| **JuiceSSH** | Free / Pro | ⭐⭐⭐⭐⭐ | Long-standing, rich plugin ecosystem |
| **ConnectBot** | Open source, free | ⭐⭐⭐⭐ | Lightweight, no ads |
| **SimpleSSH** | Free | ⭐⭐⭐⭐ | Simple and easy to use |

**Recommendation:** Termius or JuiceSSH

### 🚀 Quick Start (Termius)

#### 1. Install

- iOS: search "Termius" in the App Store
- Android: Google Play or Coolapk

#### 2. Add a Host

```
1. Open Termius → tap "+" in the top-right corner
2. Choose "New Host"
3. Fill in:
   - Alias: My Server (any name)
   - Hostname: 192.168.1.100 (IP or domain)
   - Port: 22 (default SSH port)
   - Username: root (or your username)

4. Authentication (pick one):

   Option A - Password:
   - Password: enter your password

   Option B - SSH key (recommended):
   - Tap "Key" → "Generate Key" or import an existing key
```

#### 3. Connect

- Tap the saved host
- On first connect you'll be prompted to save the host key → tap "Accept"
- Connected! You can now type commands

### 🔑 Key Authentication Setup

**Generate a key pair (recommended):**

```bash
# In Termius:
Settings → Keychain → Generate Key → RSA/Ed25519

# Copy the public key to the server:
# Option 1: manual copy
1. In Termius tap the key → Copy Public Key
2. Log into the server and append it to ~/.ssh/authorized_keys

# Option 2: use Termius' built-in feature
1. Connect and enter the password
2. Termius will prompt to save the key
3. Tap "Copy ID" to deploy the public key automatically
```

### 💡 Advanced Features

**SFTP file transfer:**

```
After connecting in Termius → swipe left → choose "SFTP"
You can:
- Upload / download files
- Browse the remote file system
- Edit text files
```

**Port forwarding (Tunnel):**

```
Use case: access an internal service
Settings → Port Forwarding → Add Rule
Local Port: 8080
Remote Host: localhost
Remote Port: 80

Then open localhost:8080 in your phone's browser
```

---

## 2. Remote Desktop

Remote desktop lets you control a computer's graphical interface from your phone.

### 🖥️ Protocol Selection

| Protocol | Use Case | Pros | Cons |
|----------|----------|------|------|
| **RDP** | Windows servers | Efficient, native support | Linux needs configuration |
| **VNC** | Linux/Mac/Windows | Cross-platform | Slower, less secure |
| **TeamViewer** | Cross-platform / remote assistance | Traverses NAT, easy to use | Commercial software |
| **Chrome Remote Desktop** | Personal use | Free, simple | Requires Chrome |

### 📱 Recommended Apps

#### iOS

| App | Protocol | Highlights |
|-----|---------|-----------|
| **Microsoft Remote Desktop** | RDP | Official Microsoft, smooth |
| **VNC Viewer** | VNC | Official RealVNC client |
| **TeamViewer** | Proprietary | First choice for remote assistance |
| **AnyDesk** | Proprietary | Lightweight, fast |
| **Chrome Remote Desktop** | Proprietary | Requires Chrome extension |

#### Android

| App | Protocol | Highlights |
|-----|---------|-----------|
| **Microsoft Remote Desktop** | RDP | Official Microsoft |
| **VNC Viewer** | VNC | Official client |
| **TeamViewer** | Proprietary | Full-featured |
| **AnyDesk** | Proprietary | Low latency |
| **RustDesk** | Proprietary | Open-source alternative |

### 🚀 Quick Start

#### Option A: Windows RDP (LAN)

**On the computer:**

```
1. Windows Settings → System → Remote Desktop → Enable
2. Note the computer name or IP address
3. Ensure the phone and computer are on the same network (or configure port forwarding)
```

**On the phone:**

```
1. Install Microsoft Remote Desktop
2. Tap "+" → "Desktop"
3. PC Name: 192.168.1.100 (or computer name)
4. User Account: add Windows username and password
5. Tap to connect
```

#### Option B: TeamViewer (across networks)

**On the computer:**

```
1. Download TeamViewer (teamviewer.com)
2. Install and run
3. Get the ID and password
```

**On the phone:**

```
1. Install the TeamViewer app
2. Enter the computer's Partner ID
3. Enter the password
4. Connected!
```

**Highlights:**

- ✅ No router configuration needed
- ✅ Traverses NAT
- ✅ File transfer
- ✅ Voice call

#### Option C: RustDesk (open source, free)

**Self-hosted server (optional):**

```bash
# Deploy with Docker
docker run --net=host rustdesk/rustdesk-server-hbbr
docker run --net=host rustdesk/rustdesk-server-hbbs
```

**Usage:**

- Similar to TeamViewer
- Completely free
- Data is controllable (you can self-host the server)

---

## 3. Phone-to-Phone Control

Used to remotely assist family and friends with phone issues.

### 📱 Recommended Solutions

#### TeamViewer QuickSupport (cross-platform)

**Controlled side (the phone needing help):**

```
1. Install TeamViewer QuickSupport
2. Open it and it will show an ID
```

**Controlling side (your phone):**

```
1. Install TeamViewer
2. Enter the other party's ID
3. Once they accept, you can control it
```

**Limitations:**

- iOS cannot be remotely controlled (OS restriction), only screen sharing
- Android can be fully controlled remotely (requires enabling accessibility services)

#### AirDroid (files + remote)

**Features:**

- File transfer
- SMS management
- Remote camera
- Screen mirroring

#### Scrcpy (Android screen mirroring)

**Control a phone from a computer:**

```bash
# Requires a computer
scrcpy --tcpip=192.168.1.100:5555
```

**Then you can view the mirrored screen on the phone**

---

## 4. Command-Line Approach

Run a full Linux environment on your phone.

### iOS: iSH (free)

```
Search "iSH" in the App Store

Features:
- Local Alpine Linux environment
- Install software: apk add openssh
- Can SSH to other servers
- Supports Python, Git, etc.
```

**Install an SSH client:**

```bash
# In iSH
apk update
apk add openssh-client

# Connect to a server
ssh user@hostname
```

### Android: Termux (open source, free)

```
Download from F-Droid or GitHub (the Play Store version is older)

Features:
- Full Linux environment
- Package managers: pkg / apt
- Supports Python, Node.js, Git
- Can install OpenSSH
```

**Termux SSH connection:**

```bash
# Install OpenSSH
pkg install openssh

# Generate a key
ssh-keygen -t ed25519

# Connect to a server
ssh user@hostname

# Use a key
ssh -i ~/.ssh/id_ed25519 user@hostname
```

**Termux advanced usage:**

```bash
# Install a full dev environment
pkg install git python nodejs vim

# Install remote control tools
pkg install tmux mosh

# Use tmux to keep a session alive
tmux new -s mysession

# Use mosh for low-latency connections
mosh user@hostname
```

---

## 5. Security Configuration

### 🔐 Basic Security

#### 1. Use key authentication (disable passwords)

**On the server:**

```bash
# Edit the SSH config
sudo nano /etc/ssh/sshd_config

# Change the following
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password

# Restart SSH
sudo systemctl restart sshd
```

#### 2. Use a non-standard port

```bash
# Change the SSH port to 2222
Port 2222

# Allow it through the firewall
sudo ufw allow 2222/tcp
```

#### 3. Use Fail2ban to prevent brute force

```bash
# Install
sudo apt install fail2ban

# Configure
sudo nano /etc/fail2ban/jail.local

[sshd]
enabled = true
port = 2222
maxretry = 3
bantime = 3600
```

### 🛡️ Advanced Security

#### VPN approach

**WireGuard (recommended):**

```bash
# Install WireGuard
sudo apt install wireguard

# Generate a key pair
wg genkey | tee privatekey | wg pubkey > publickey

# Configure the server
sudo nano /etc/wireguard/wg0.conf

[Interface]
PrivateKey = <server private key>
Address = 10.0.0.1/24
ListenPort = 51820
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT

[Peer]
PublicKey = <phone public key>
AllowedIPs = 10.0.0.2/32

# Start
sudo wg-quick up wg0
```

**On the phone:**

- iOS: search "WireGuard" in the App Store
- Android: search "WireGuard" in the Play Store
- Import the config and you're done

#### Bastion Host

```
Scenario: protect internal servers

Internet → Bastion Host (public IP) → Internal Server

The phone connects only to the bastion, which then connects to the internal server
```

**SSH configuration:**

```
# In Termius
1. Add the bastion host first
2. Add the internal server, set:
   - Gateway / Jump Host: select the bastion host

3. When connecting to the internal server, it goes through the bastion automatically
```

### 📱 Phone-side Security

#### 1. Enable an app lock

```
iOS: Settings → Screen Time → App Limits
Android: Settings → App Lock
```

#### 2. Use a password manager

- 1Password
- Bitwarden
- iOS Keychain

#### 3. Clean up keys regularly

```
Termius: Settings → Keychain → delete keys you no longer use
```

---

## 6. App Comparison & Recommendations

### 📊 Overall Comparison

| App | Platform | Protocol | Price | Rating | Best For |
|-----|----------|----------|-------|--------|----------|
| **Termius** | iOS/Android | SSH | Free / Sub | ⭐⭐⭐⭐⭐ | SSH/SFTP first choice |
| **Blink Shell** | iOS | SSH/Mosh | Paid | ⭐⭐⭐⭐⭐ | Professional dev |
| **JuiceSSH** | Android | SSH | Free/Pro | ⭐⭐⭐⭐⭐ | Android SSH |
| **TeamViewer** | All | Proprietary | Free/Commercial | ⭐⭐⭐⭐⭐ | Remote assistance |
| **AnyDesk** | All | Proprietary | Free/Commercial | ⭐⭐⭐⭐ | Fast connection |
| **RustDesk** | All | Proprietary | Open source, free | ⭐⭐⭐⭐ | Self-hosted |
| **Microsoft RDP** | iOS/Android | RDP | Free | ⭐⭐⭐⭐⭐ | Windows remote |
| **VNC Viewer** | All | VNC | Free | ⭐⭐⭐⭐ | Linux/Mac remote |
| **iSH** | iOS | Local | Free | ⭐⭐⭐⭐⭐ | Local Linux |
| **Termux** | Android | Local | Open source | ⭐⭐⭐⭐⭐ | Local Linux |

### 🎯 Recommendations by Scenario

#### System administrators

- **Primary:** Termius + WireGuard VPN
- **Backup:** JuiceSSH (Android) + Blink Shell (iOS)
- **Emergency:** Termux (offline doc lookup)

#### Developers

- **Primary:** Blink Shell (iOS) / Termux (Android)
- **Companion:** GitHub App + Working Copy (Git client)
- **Debugging:** iSH (local testing)

#### Home users

- **Primary:** TeamViewer (simple and easy)
- **Alternative:** RustDesk (free, no ads)
- **Phone-to-phone:** TeamViewer QuickSupport

#### Enthusiasts

- **Primary:** Termux (full Linux) + Termius (SSH)
- **Explore:** UserLAnd (full Linux distro)
- **Self-host:** RustDesk server

---

## 7. Real-World Examples

### Scenario 1: Urgently fix a production bug

```
Time: 2 AM
Place: in bed at home
Device: iPhone

Steps:
1. Receive an alert SMS — server CPU at 100%
2. Open Termius → tap the production server
3. Auto-connect (key auth)
4. Run commands:
   $ top
   $ ps aux | grep python
   $ kill -9 <PID>
5. Check logs:
   $ tail -f /var/log/app/error.log
6. Restart the service:
   $ sudo systemctl restart app
7. Verify:
   $ curl http://localhost:8080/health
8. Problem solved — total time 3 minutes
```

### Scenario 2: Remotely help parents fix their computer

```
Target: parents' Windows PC
Problem: don't know how to install a printer driver

Steps:
1. Have parents download TeamViewer QuickSupport
2. Open it and tell you the ID and password
3. Open TeamViewer on your phone
4. Enter parents' ID to connect
5. Remotely install the driver
6. Disconnect when done

Pros:
- Parents need no technical knowledge
- Visual operation, simple and intuitive
- Can guide via voice call
```

### Scenario 3: Deploy code on the subway

```
Scenario: on the way home, need an urgent release

Steps:
1. Open Termius on the subway
2. Connect to bastion → connect to production server
3. Run the deploy script:
   $ cd /var/www/app
   $ git pull origin main
   $ ./deploy.sh
4. Check deploy logs:
   $ tail -f deploy.log
5. Health check:
   $ curl -s http://localhost/health
6. Release complete ✅
```

### Scenario 4: Build a dev environment on your phone

```
Device: Android phone
Goal: a full Python dev environment

Steps:
1. Install Termux
2. Install dev tools:
   $ pkg install git python vim
3. Clone the project:
   $ git clone https://github.com/user/project.git
4. Install dependencies:
   $ cd project
   $ pip install -r requirements.txt
5. Edit code:
   $ vim main.py
6. Run tests:
   $ python -m pytest
7. Push to GitHub:
   $ git add .
   $ git commit -m "fix: bug"
   $ git push
```

---

## 8. FAQ

### Q1: Why can't iOS be remotely controlled like Android?

**A:** iOS security restrictions do not allow a background app to control the screen. Only screen sharing is possible.

### Q2: SSH connection failed — what now?

**Troubleshooting steps:**

```
1. Check the network: ping <server IP>
2. Check the port: nc -zv <IP> 22
3. Check the service: systemctl status sshd on the server
4. Check the firewall: ufw status / iptables -L
5. Check logs: /var/log/auth.log on the server
```

### Q3: How to improve remote desktop smoothness?

**Optimizations:**

- Lower resolution and color quality
- Use a wired network instead of WiFi
- Disable visual effects on the remote computer
- Use a dedicated line / VPN (avoid public-internet latency)

### Q4: Phone battery drains fast — what to do?

**Suggestions:**

- Disconnect when not in use
- Lower screen brightness
- Use dark mode
- Carry a power bank

### Q5: How to keep servers safe if the phone is lost?

**Measures:**

1. Set a strong password / Face ID on the phone
2. Enable an app lock in Termius
3. Use key auth (not passwords) on the server
4. Configure Fail2ban on the server
5. Rotate keys regularly
6. Enable remote wipe on the phone

---

## 9. Advanced Tips

### Use Shortcuts (iOS)

```
Create a shortcut for one-tap connection:

1. Open the "Shortcuts" app
2. Create a new shortcut
3. Add actions:
   - URL: ssh://user@hostname
   - Open URL
4. Add to Home Screen
5. Tap the icon to open Termius and connect directly
```

### Use Siri voice connection

```
"Hey Siri, connect to my server"

Setup:
1. Create a shortcut (as above)
2. Tap "Settings" → "Add to Siri"
3. Record the voice command
```

### Automation scripts (Termux)

```bash
# Create a connection script
nano ~/connect.sh

#!/bin/bash
echo "Connecting to server..."
ssh -i ~/.ssh/id_ed25519 user@hostname

# Add to a shortcut
termux-shortcuts
# Then create a shortcut on the home screen
```

---

## Conclusion

Phone-based remote control makes "managing devices anytime, anywhere" possible. Whether handling emergencies, assisting family and friends, or just enjoying mobile productivity, the right tool makes the job far easier.

**Remember three principles:**

1. 🔐 **Security first** — use keys, VPN, and strong passwords
2. ⚡ **Efficiency above all** — choose low-latency, easy-to-operate tools
3. 📱 **Fit the scenario** — pick a solution based on real needs

---

## Contributing

Pull requests with more solutions and experiences are welcome!

**Content still needed:**

- Detailed reviews of more apps
- Setup tutorials for specific scenarios
- Solutions to tricky problems

---

**Last updated:** 2024

**Maintainer:** Vae-Scrooge

**License:** MIT
