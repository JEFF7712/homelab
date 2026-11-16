{
  catch_all_is_last."assert" = [
    {
      condition = "\${local.ingress[length(local.ingress) - 1].service == \"http_status:404\" && local.ingress[length(local.ingress) - 1].hostname == null}";
      error_message = "the final ingress rule must be the hostname-less catch-all, otherwise unmatched hosts stop returning 404";
    }
  ];
  only_the_catch_all_lacks_a_hostname."assert" = [
    {
      condition = "\${length([for rule in local.ingress : rule.hostname if rule.hostname == null]) == 1}";
      error_message = "a rule other than the trailing catch-all has no hostname, so Cloudflare would match it as a catch-all and shadow every later rule";
    }
  ];
}
