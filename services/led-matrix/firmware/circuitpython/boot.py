import usb_cdc

# Enable both the REPL console and a dedicated raw data serial endpoint (/dev/ttyACM1)
usb_cdc.enable(console=True, data=True)
