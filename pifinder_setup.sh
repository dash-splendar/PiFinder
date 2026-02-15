#!/usr/bin/bash
# This script installs the PiFinder software on a prepared Raspberry Pi OS.
# See https://pifinder.readthedocs.io/en/release/software.html for more info.

set -e

cd ~pifinder/

sudo apt-get install -y git python3-pip samba samba-common-bin dnsmasq hostapd dhcpd gpsd python3-dev build-essential libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev libfreetype6-dev libportmidi-dev xserver-xorg xinit x11-xserver-utils libgl1-mesa-dri

if [[ -d PiFinder/ ]]; then
    cd PiFinder/ && git config pull.rebase false && git pull
else
    git clone --recursive --branch release https://github.com/dash-splendar/PiFinder.git
fi


# -----------------------------
# Create X session for PiFinder
# -----------------------------

XINITRC="/home/pifinder/.xinitrc"

sudo -u pifinder bash -c "cat > $XINITRC" << 'EOF'
#!/bin/sh
cd /home/pifinder/PiFinder/python

# Activate virtual environment
. /home/pifinder/PiFinder/python/.venv/bin/activate

# Force SDL to use X11
export SDL_VIDEODRIVER=x11

# Disable screen blanking / DPMS
xset s off
xset -dpms
xset s noblank

exec python -m PiFinder.main --keyboard touch --display hyperpixel4_native --imu usb
EOF

sudo chown pifinder:pifinder $XINITRC
sudo chmod 755 $XINITRC


# -----------------------------
# Create & use Python venv
# -----------------------------
cd ~/PiFinder/python

# Create virtual environment if it doesn't already exist
if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
fi

# Activate the virtual environment
source .venv/bin/activate

# Upgrade pip inside the venv
pip install --upgrade pip

# Install requirements inside venv
pip install -r requirements.txt

# Deactivate when done
deactivate

# Setup GPSD
sudo dpkg-reconfigure -plow gpsd
sudo cp ~/PiFinder/pi_config_files/gpsd.conf /etc/default/gpsd

# data dirs
[[ -d ~/PiFinder_data ]] || \
mkdir ~/PiFinder_data
[[ -d ~/PiFinder_data/captures ]] || \
mkdir ~/PiFinder_data/captures
[[ -d ~/PiFinder_data/obslists ]] || \
mkdir ~/PiFinder_data/obslists
[[ -d ~/PiFinder_data/screenshots ]] || \
mkdir ~/PiFinder_data/screenshots
[[ -d ~/PiFinder_data/solver_debug_dumps ]] || \
mkdir ~/PiFinder_data/solver_debug_dumps
[[ -d ~/PiFinder_data/logs ]] || \
mkdir ~/PiFinder_data/logs
find ~/PiFinder_data -type d -exec chmod 755 {} \;

# Wifi config
sudo cp ~/PiFinder/pi_config_files/dhcpcd.* /etc
sudo cp ~/PiFinder/pi_config_files/dhcpcd.conf.sta /etc/dhcpcd.conf
sudo cp ~/PiFinder/pi_config_files/dnsmasq.conf /etc/dnsmasq.conf
sudo cp ~/PiFinder/pi_config_files/hostapd.conf /etc/hostapd/hostapd.conf
echo -n "Client" > ~/PiFinder/wifi_status.txt
sudo systemctl unmask hostapd

# open permissisons on wpa_supplicant file so we can adjust network config
sudo chmod 666 /etc/wpa_supplicant/wpa_supplicant.conf

# Samba config
sudo cp ~/PiFinder/pi_config_files/smb.conf /etc/samba/smb.conf

# Hipparcos catalog
HIP_MAIN_DAT="/home/pifinder/PiFinder/astro_data/hip_main.dat"
if [[ ! -e $HIP_MAIN_DAT ]]; then
    wget -O $HIP_MAIN_DAT https://cdsarc.cds.unistra.fr/ftp/cats/I/239/hip_main.dat
fi

# Enable interfaces
#DSEdit - we are not going to use any of these natively on the Raspi
#grep -q "dtparam=spi=on" /boot/config.txt || echo "dtparam=spi=on" | sudo tee -a /boot/config.txt
#grep -q "dtparam=i2c_arm=on" /boot/config.txt || echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt
#grep -q "dtparam=i2c_arm_baudrate=10000" /boot/config.txt || echo "dtparam=i2c_arm_baudrate=10000" | sudo tee -a /boot/config.txt
#grep -q "dtoverlay=pwm,pin=13,func=4" /boot/config.txt || echo "dtoverlay=pwm,pin=13,func=4" | sudo tee -a /boot/config.txt
#grep -q "dtoverlay=uart3" /boot/config.txt || echo "dtoverlay=uart3" | sudo tee -a /boot/config.txt
# Note: camera types are added lateron by python/PiFinder/switch_camera.py

# Disable unwanted services
sudo systemctl disable ModemManager

# Enable service
sudo cp /home/pifinder/PiFinder/pi_config_files/pifinder.service /lib/systemd/system/pifinder.service
sudo cp /home/pifinder/PiFinder/pi_config_files/pifinder_splash.service /lib/systemd/system/pifinder_splash.service
sudo cp /home/pifinder/PiFinder/pi_config_files/cedar_detect.service /lib/systemd/system/cedar_detect.service
sudo systemctl daemon-reload
sudo systemctl enable cedar_detect
sudo systemctl enable pifinder
sudo systemctl disable pifinder_splash
sudo systemctl disable --now getty@tty1.service
sudo systemctl enable --now getty@tty2.service


echo "PiFinder setup complete, please restart the Pi"

