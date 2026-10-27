{
  nixpkgs,
  impermanence,
  sops-nix,
  system,
}:
let
  pkgs = nixpkgs.legacyPackages.${system};
  fixture =
    pkgs.runCommand "zot-service-contract-image" { nativeBuildInputs = [ pkgs.python313 ]; }
      ''
        python - <<'PY'
        import os
        import runpy
        from pathlib import Path

        build_image = runpy.run_path("${../../scripts/registry/gc_fixture.py}")["build_image"]
        root = Path(os.environ["out"])
        root.mkdir()
        digest = build_image(root / "image", b"zot NixOS service contract\n")
        (root / "digest").write_text(digest)
        PY
      '';
  policy = pkgs.writeText "zot-test-access-control.json" (
    builtins.toJSON {
      repositories = {
        "**".defaultPolicy = [ ];
        "contract/**" = {
          defaultPolicy = [ ];
          policies = [
            {
              users = [ "reader" ];
              actions = [ "read" ];
            }
            {
              users = [ "publisher" ];
              actions = [
                "read"
                "create"
                "update"
              ];
            }
          ];
        };
      };
      adminPolicy = {
        users = [ "maintenance" ];
        actions = [
          "read"
          "create"
          "update"
          "delete"
        ];
      };
    }
  );
  verify = pkgs.writeText "verify-zot-roundtrip.py" ''
    import hashlib
    import json
    from pathlib import Path

    source = Path("${fixture}/image")
    pulled = Path("/tmp/pulled")
    expected = json.loads((source / "index.json").read_text())["manifests"][0]
    actual = json.loads((pulled / "index.json").read_text())["manifests"][0]
    assert actual["digest"] == expected["digest"]
    manifest = json.loads((source / "blobs/sha256" / expected["digest"][7:]).read_bytes())
    for descriptor in [expected, manifest["config"], *manifest["layers"]]:
        relative = "blobs/sha256/" + descriptor["digest"][7:]
        data = (pulled / relative).read_bytes()
        assert len(data) == descriptor["size"]
        assert "sha256:" + hashlib.sha256(data).hexdigest() == descriptor["digest"]
        assert data == (source / relative).read_bytes()
  '';
in
pkgs.testers.nixosTest {
  name = "zot-registry-service-contract";
  nodes.machine =
    { lib, ... }:
    {
      virtualisation.host.pkgs = pkgs.lib.mkForce (import ./vm-host-pkgs.nix { inherit pkgs; });
      imports = [
        impermanence.nixosModules.impermanence
        sops-nix.nixosModules.sops
        ../modules/zot-registry.nix
      ];
      services.homelab-zot-registry = {
        enable = true;
        hostName = "registry.test";
        acmeEmail = "drill@example.invalid";
        htpasswdFile = "/run/zot-test/htpasswd";
        accessControlFile = "/run/zot-test/access-control.json";
        cloudflareTokenFile = "/dev/null";
      };
      sops.secrets = lib.mkForce { };
      networking.hosts = lib.mkForce { "127.0.0.1" = [ "registry.test" ]; };
      security.acme.certs = lib.mkForce { };
      services.nginx.virtualHosts."registry.test" = {
        useACMEHost = lib.mkForce null;
        sslCertificate = "/run/zot-test/ca.crt";
        sslCertificateKey = "/run/zot-test/key.pem";
      };
      virtualisation.fileSystems."/tank/registry" = {
        device = "none";
        fsType = "tmpfs";
      };
      virtualisation.fileSystems."/persist" = {
        device = "none";
        fsType = "tmpfs";
        neededForBoot = true;
      };
      systemd.services.zot-test-material = {
        requiredBy = [
          "zot.service"
          "nginx.service"
        ];
        before = [
          "zot.service"
          "nginx.service"
        ];
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          RuntimeDirectory = "zot-test";
          RuntimeDirectoryMode = "0750";
        };
        path = [
          pkgs.apacheHttpd
          pkgs.openssl
        ];
        script = ''
          umask 077
          touch /run/zot-test/htpasswd
          for user in reader publisher; do
            password=$(openssl rand -hex 16)
            printf '%s\n' "$password" | htpasswd -Bi /run/zot-test/htpasswd "$user"
            printf '%s:%s' "$user" "$password" > "/run/zot-test/$user.auth"
          done
          cp ${policy} /run/zot-test/access-control.json
          openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
            -subj /CN=registry.test -addext subjectAltName=DNS:registry.test \
            -addext basicConstraints=critical,CA:TRUE \
            -keyout /run/zot-test/key.pem -out /run/zot-test/ca.crt
          chown root:nginx /run/zot-test /run/zot-test/key.pem
          chmod 0640 /run/zot-test/key.pem
          chmod 0644 /run/zot-test/ca.crt
        '';
      };
      environment.systemPackages = [
        pkgs.curl
        pkgs.skopeo
        pkgs.python313
      ];
      environment.etc."zot-contract/image".source = fixture;
      environment.etc."zot-contract/verify.py".source = verify;
      environment.etc."containers/policy.json".text = builtins.toJSON {
        default = [ { type = "reject"; } ];
        transports = {
          oci."" = [ { type = "insecureAcceptAnything"; } ];
          docker."registry.test" = [ { type = "insecureAcceptAnything"; } ];
        };
      };
      virtualisation.memorySize = 1024;
      system.stateVersion = "26.05";
    };
  testScript = ''
    machine.start()
    machine.wait_for_unit("zot.service")
    machine.wait_for_unit("nginx.service")
    machine.wait_for_open_port(443)
    machine.wait_for_open_port(5000)
    machine.succeed("mountpoint -q /tank/registry")
    reader = machine.succeed("cat /run/zot-test/reader.auth").strip()
    publisher = machine.succeed("cat /run/zot-test/publisher.auth").strip()
    curl = "curl --silent --show-error --cacert /run/zot-test/ca.crt"
    endpoint = "https://registry.test/v2/"
    # A listening nginx is not readiness evidence for its registry upstream.
    machine.wait_until_succeeds(f"{curl} -o /dev/null -w '%{{http_code}}' {endpoint} | grep -qx 401", timeout=120)
    for credentials in ("", f"--user {reader}incorrect"):
        status = machine.succeed(f"{curl} {credentials} -o /dev/null -w '%{{http_code}}' {endpoint}").strip()
        assert status == "401", f"unauthenticated registry returned {status}, expected 401"
    status = machine.succeed(f"{curl} --user {reader} -o /dev/null -w '%{{http_code}}' {endpoint}").strip()
    assert status == "200", f"reader registry request returned {status}, expected 200"

    fixture = "/etc/zot-contract/image"
    push = f"skopeo copy --preserve-digests --dest-cert-dir /run/zot-test oci:{fixture}/image:v1"
    def denied(command):
        output = machine.fail(command + " 2>&1")
        assert any(term in output.lower() for term in ("denied", "unauthorized", "403", "401")), output
    denied(f"{push} --dest-creds {reader} docker://registry.test/contract/image:v1")
    denied(f"{push} --dest-creds {publisher} docker://registry.test/forbidden/image:v1")
    machine.succeed(f"{push} --dest-creds {publisher} docker://registry.test/contract/image:v1")
    digest = machine.succeed(f"cat {fixture}/digest").strip()
    pull = f"skopeo copy --preserve-digests --src-cert-dir /run/zot-test --src-creds {reader} docker://registry.test/contract/image@{digest} oci:/tmp/pulled:v1"
    machine.succeed(pull)
    machine.succeed("python /etc/zot-contract/verify.py")

    machine.succeed("systemctl restart zot.service")
    machine.wait_for_unit("zot.service")
    machine.wait_for_open_port(5000)
    machine.wait_until_succeeds(f"{curl} -o /dev/null -w '%{{http_code}}' {endpoint} | grep -qx 401", timeout=120)
    status = machine.succeed(f"{curl} -o /dev/null -w '%{{http_code}}' {endpoint}").strip()
    assert status == "401", f"restarted registry returned {status}, expected 401"
    machine.succeed("rm -rf /tmp/pulled")
    machine.succeed(pull)
    machine.succeed("python /etc/zot-contract/verify.py")
    denied(f"{push} --dest-creds {reader} docker://registry.test/contract/image:v2")
  '';
}
