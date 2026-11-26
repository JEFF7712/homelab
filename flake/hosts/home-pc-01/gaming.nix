# Games: Steam with Proton for the Steam library, and Wine with the usual
# front ends for Windows games bought elsewhere (Epic, GOG, Amazon,
# installers). 32-bit graphics come from hardware.graphics.enable32Bit.
{ pkgs, ... }:
{
  programs.steam = {
    enable = true;
    extraCompatPackages = [ pkgs.proton-ge-bin ];
    # Winetricks for individual Proton games.
    protontricks.enable = true;
  };
  # Valve's compositor: fixes resolution, windowing and frame pacing quirks.
  programs.gamescope = {
    enable = true;
    capSysNice = true;
  };
  programs.gamemode.enable = true;

  environment.systemPackages = with pkgs; [
    # Wine (staging, WoW64) and Windows runtime installers.
    wine-staging
    winetricks
    # Launchers: Lutris (any store or installer), Heroic (Epic, GOG,
    # Amazon), Bottles (one isolated prefix per app or game).
    lutris
    heroic
    bottles
    # Overlay, Vulkan diagnostics, Minecraft, flight sim.
    mangohud
    vulkan-tools
    prismlauncher
    flightgear
    # jstest/jscal for checking the Honeycomb Alpha and Bravo.
    linuxConsoleTools
  ];
}
