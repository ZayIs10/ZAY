# Oracle Cloud Free Tier — Permanent Free Remote Claude Code Host

This gives you a **permanent free** Linux VM with 4 ARM CPU cores and 24 GB RAM. You SSH into it from your laptop and run Claude Code there — your local machine only runs Cursor as a thin frontend, so HyperFrames renders never compete for resources.

**Total cost: $0 forever.** Oracle requires a credit card to verify, but it never charges as long as you stay on Always Free resources.

---

## Step 1 — Sign up (15 minutes)

1. Go to https://signup.cloud.oracle.com/
2. Pick a **Home Region** carefully — you cannot change it. Choose the one closest to you (e.g. `Frankfurt` if you're in Europe, `Phoenix`/`Ashburn` for US).
3. Fill in details. Use a real address (Oracle verifies).
4. Add a credit card. **Important:** they put a $1 hold and refund it; they do NOT charge for Always Free resources, but you must select **Always Free** when creating things.
5. Account activation usually takes 5–10 minutes.

## Step 2 — Create the VM (10 minutes)

Once logged into the Oracle Cloud Console:

1. Top-left hamburger menu → **Compute** → **Instances**
2. Click **Create instance**
3. Name: `claude-code-host`
4. **Image and shape** → Edit:
   - Image: **Canonical Ubuntu 22.04** (or 24.04)
   - Shape: click **Change shape** → **Ampere** tab → select **VM.Standard.A1.Flex**
   - Bump OCPUs to **4** and Memory to **24 GB** (this is the free max)
5. **Networking** → leave defaults (creates a new VCN automatically)
6. **Add SSH keys** → choose **Generate a key pair for me** → click **Save private key** and **Save public key** (download both — you need the private key to log in)
7. **Boot volume** → leave default (50 GB free)
8. Click **Create**

Wait ~2 minutes for it to provision. Once it shows **Running**, copy the **Public IP address** from the instance page.

## Step 3 — Connect from your laptop (5 minutes)

Open PowerShell:

```powershell
# Move the downloaded private key somewhere safe
mkdir $env:USERPROFILE\.ssh -ErrorAction SilentlyContinue
move "$env:USERPROFILE\Downloads\ssh-key-*.key" "$env:USERPROFILE\.ssh\oracle_claude.key"

# Lock down permissions (Windows equivalent of chmod 600)
icacls "$env:USERPROFILE\.ssh\oracle_claude.key" /inheritance:r /grant:r "$($env:USERNAME):(R)"

# Connect (replace the IP with yours)
ssh -i $env:USERPROFILE\.ssh\oracle_claude.key ubuntu@<YOUR_PUBLIC_IP>
```

If it works, you'll get an `ubuntu@claude-code-host:~$` prompt. You're in.

## Step 4 — Install Claude Code on the VM (10 minutes)

Once SSH'd in:

```bash
# System update
sudo apt update && sudo apt upgrade -y

# Install Node.js 20 (required for Claude Code)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs git tmux

# Install Claude Code
sudo npm install -g @anthropic-ai/claude-code

# Verify
claude --version
```

## Step 5 — Open the firewall for SSH (already open) and authenticate Claude Code

```bash
# Just run claude — it will prompt you to log in
claude
```

It will print a URL — open it on your laptop, log in to your Anthropic account, paste the auth code back into the SSH session.

## Step 6 — Daily workflow

**On your laptop:**
```powershell
ssh -i $env:USERPROFILE\.ssh\oracle_claude.key ubuntu@<YOUR_PUBLIC_IP>
```

**On the VM (use tmux so sessions survive disconnects):**
```bash
tmux new -s work
cd ~/your-project
claude
```

Detach with `Ctrl+B then D`. Reattach with `tmux attach -t work`.

To get your Gen Z project files onto the VM, either:
- Clone from GitHub: `git clone https://github.com/<you>/<repo>.git`
- Or use VS Code's Remote-SSH extension (Cursor supports this too) to edit files directly on the VM as if they were local

## Step 7 — Cursor with Remote-SSH (the magic part)

This makes Cursor edit files on the VM but render them in your local UI:

1. In Cursor: Ctrl+Shift+P → **Remote-SSH: Connect to Host**
2. Add new host: `ssh ubuntu@<YOUR_PUBLIC_IP> -i C:\Users\Marc\.ssh\oracle_claude.key`
3. Connect — Cursor will install its server on the VM automatically
4. Open your project folder on the VM
5. Open the integrated terminal → `claude` — you're now running Claude Code on Oracle's hardware while editing in your local Cursor

**Result:** HyperFrames gets your full local 32 GB RAM and 4 GB VRAM. Claude Code uses Oracle's 24 GB RAM and 4 CPU cores. They never compete.

---

## Things to watch out for

- **ARM architecture:** the A1.Flex VM is ARM, not x86. Most Node/Python tools work fine, but some npm packages with native binaries may need `--platform=linux/arm64` builds. If you hit issues, fall back to the **VM.Standard.E2.1.Micro** x86 free shape (only 1 GB RAM, slower, but compatible with everything).
- **Capacity errors:** ARM A1.Flex is popular and sometimes "Out of capacity" in your region. If create fails, try a different availability domain or wait a few hours.
- **Don't shut the VM down via the OS** — use the Oracle Console to stop/start so it doesn't get reclaimed.
- **Set a billing alert** anyway: Console → Billing → Cost Management → Budgets → create a $1 budget that alerts you if anything ever charges.
