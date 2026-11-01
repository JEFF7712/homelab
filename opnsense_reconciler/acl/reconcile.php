<?php
require_once '/usr/local/opnsense/mvc/script/load_phalcon.php';

const BASE = '/usr/local/opnsense/mvc/app/models/Homelab';
const DESTINATION = BASE . '/AgentDiagnostics/ACL/ACL.xml';
const PRIVILEGE = 'page-homelab-agent-bgp-readonly';
const NAME = 'Homelab: Read-only troubleshooting';
const PATTERNS = [
    'api/core/service/search',
    'api/diagnostics/firewall/list_rule_ids',
    'api/diagnostics/firewall/log',
    'api/diagnostics/firewall/pf_states',
    'api/diagnostics/firewall/pf_statistics',
    'api/diagnostics/firewall/query_pf_top',
    'api/diagnostics/firewall/query_states',
    'api/diagnostics/firewall/stats',
    'api/diagnostics/interface/get_arp',
    'api/diagnostics/interface/get_interface_config',
    'api/diagnostics/interface/get_interface_names',
    'api/diagnostics/interface/get_interface_statistics',
    'api/diagnostics/interface/get_ndp',
    'api/diagnostics/interface/get_routes',
    'api/diagnostics/interface/get_vip_status',
    'api/diagnostics/interface/search_arp',
    'api/diagnostics/interface/search_ndp',
    'api/diagnostics/system/memory',
    'api/diagnostics/system/system_disk',
    'api/diagnostics/system/system_information',
    'api/diagnostics/system/system_resources',
    'api/dnsmasq/leases/search',
    'api/dnsmasq/service/status',
    'api/firewall/alias/get_table_size',
    'api/firewall/alias/search_item',
    'api/firewall/d_nat/search_rule',
    'api/firewall/filter/search_rule',
    'api/firewall/filter_util/rule_stats',
    'api/firewall/npt/search_rule',
    'api/firewall/one_to_one/search_rule',
    'api/firewall/source_nat/search_rule',
    'api/kea/leases4/search',
    'api/kea/leases6/search',
    'api/kea/service/status',
    'api/quagga/diagnostics/bgpsummary',
    'api/quagga/diagnostics/search_bgproute4',
    'api/quagga/service/status',
    'api/routes/gateway/status',
    'api/routes/routes/searchroute',
    'api/unbound/diagnostics/stats',
    'api/unbound/overview/is_enabled',
    'api/unbound/overview/search_queries',
    'api/unbound/service/status',
];
const RECOVERY = '/conf/backup/homelab-agent-acl';
const LOCK = '/var/run/homelab-agent-acl.lock';
const OWNER_UID = 0;
const OWNER_GID = 0;

function ensure($condition, $message) {
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

function metadata($path) {
    if (!file_exists($path)) {
        return null;
    }
    $stat = stat($path);
    return ['uid' => $stat['uid'], 'gid' => $stat['gid'], 'mode' => $stat['mode'] & 07777];
}

function directories() {
    return [BASE, BASE . '/AgentDiagnostics', BASE . '/AgentDiagnostics/ACL'];
}

function snapshot() {
    clearstatcache();
    $dirs = [];
    foreach (directories() as $path) {
        ensure(!is_link($path), 'Refusing a symlink in the ACL directory path');
        ensure(!file_exists($path) || is_dir($path), 'ACL parent path is not a directory');
        $dirs[$path] = metadata($path);
    }
    ensure(!is_link(DESTINATION), 'Refusing an ACL symlink');
    $file = null;
    if (file_exists(DESTINATION)) {
        ensure(is_file(DESTINATION) && filesize(DESTINATION) <= 65536, 'Invalid existing ACL file');
        $content = file_get_contents(DESTINATION);
        ensure($content !== false, 'Cannot read existing ACL');
        $file = metadata(DESTINATION) + ['content' => base64_encode($content), 'sha256' => hash('sha256', $content)];
    }
    $cacheFile = (new OPNsense\Core\AppConfig())->application->tempDir . '/opnsense_acl_cache.json';
    $cache = is_file($cacheFile) ? json_decode(file_get_contents($cacheFile), true) : [];
    return ['file' => $file, 'directories' => $dirs, 'cache_entry' => $cache[PRIVILEGE] ?? null];
}

function fingerprint($state) {
    return hash('sha256', json_encode($state, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
}

function drift($state, $content, $entry) {
    $reasons = [];
    $file = $state['file'];
    if ($file === null || $file['sha256'] !== hash('sha256', $content)) {
        $reasons[] = 'file_content';
    }
    if ($file === null || $file['uid'] !== OWNER_UID || $file['gid'] !== OWNER_GID || $file['mode'] !== 0644) {
        $reasons[] = 'file_metadata';
    }
    foreach ($state['directories'] as $path => $metadata) {
        if ($metadata !== ['uid' => OWNER_UID, 'gid' => OWNER_GID, 'mode' => 0755]) {
            $reasons[] = 'directory_metadata:' . $path;
        }
    }
    if ($state['cache_entry'] !== $entry) {
        $reasons[] = 'cached_privilege';
    }
    return $reasons;
}

function setMetadata($path, $metadata) {
    ensure(chown($path, $metadata['uid']), 'Cannot set ACL owner');
    ensure(chgrp($path, $metadata['gid']), 'Cannot set ACL group');
    ensure(chmod($path, $metadata['mode']), 'Cannot set ACL permissions');
}

function atomicWrite($content, $metadata, $destination = DESTINATION) {
    $temporary = tempnam(dirname($destination), '.agent-acl-');
    ensure($temporary !== false, 'Cannot stage ACL file');
    try {
        $handle = fopen($temporary, 'wb');
        ensure($handle !== false, 'Cannot open staged ACL file');
        try {
            ensure(fwrite($handle, $content) === strlen($content) && fflush($handle) && fsync($handle), 'Incomplete ACL write');
        } finally {
            fclose($handle);
        }
        setMetadata($temporary, $metadata);
        ensure(rename($temporary, $destination), 'Cannot install ACL atomically');
    } finally {
        if (is_file($temporary)) {
            unlink($temporary);
        }
    }
}

function saveRecovery($before, $content) {
    ensure(!is_link(RECOVERY), 'Invalid ACL recovery directory');
    if (!is_dir(RECOVERY)) {
        ensure(mkdir(RECOVERY, 0700), 'Cannot create ACL recovery directory');
    }
    setMetadata(RECOVERY, ['uid' => OWNER_UID, 'gid' => OWNER_GID, 'mode' => 0700]);
    $path = RECOVERY . '/' . fingerprint($before) . '-' . hash('sha256', $content) . '.json';
    $data = json_encode(['before' => $before, 'desired_sha256' => hash('sha256', $content)], JSON_THROW_ON_ERROR);
    ensure(!is_link($path), 'Invalid ACL recovery file');
    if (file_exists($path)) {
        ensure(is_file($path) && file_get_contents($path) === $data, 'Existing ACL recovery data differs');
        setMetadata($path, ['uid' => OWNER_UID, 'gid' => OWNER_GID, 'mode' => 0600]);
    } else {
        atomicWrite($data, ['uid' => OWNER_UID, 'gid' => OWNER_GID, 'mode' => 0600], $path);
    }
    return $path;
}

function refreshCache() {
    $acl = new OPNsense\Core\ACL();
    $acl->invalidateCache();
    $fresh = new OPNsense\Core\ACL();
    ensure($fresh->persist(false), 'ACL cache refresh failed');
}

function restore($before, $content) {
    $current = snapshot()['file'];
    ensure($current === null || $current['sha256'] === hash('sha256', $content) || $current['sha256'] === ($before['file']['sha256'] ?? null), 'Concurrent file change prevents rollback');
    if ($before['file'] === null) {
        if ($current !== null) {
            ensure(unlink(DESTINATION), 'Cannot remove newly installed ACL during rollback');
        }
    } else {
        atomicWrite(base64_decode($before['file']['content'], true), $before['file']);
    }
    foreach (array_reverse($before['directories'], true) as $path => $metadata) {
        if ($metadata === null) {
            if (is_dir($path)) {
                ensure(rmdir($path), 'New ACL directory is not empty during rollback');
            }
        } else {
            setMetadata($path, $metadata);
        }
    }
    refreshCache();
}

try {
    $request = json_decode(base64_decode('__REQUEST_BASE64__'), true, 512, JSON_THROW_ON_ERROR);
    ensure(in_array($request['action'] ?? '', ['inspect', 'apply'], true), 'Invalid ACL action');
    $content = base64_decode($request['content'] ?? '', true);
    ensure(is_string($content) && strlen($content) <= 65536 && strpos($content, '<!DOCTYPE') === false && strpos($content, '<!ENTITY') === false, 'Invalid ACL source');
    $xml = simplexml_load_string($content, 'SimpleXMLElement', LIBXML_NONET);
    ensure($xml !== false && $xml->getName() === 'acl' && count($xml->children()) === 1 && isset($xml->{PRIVILEGE}), 'Unexpected ACL privilege');
    $privilege = $xml->{PRIVILEGE};
    $patterns = [];
    foreach ($privilege->patterns->pattern as $pattern) {
        $patterns[] = (string)$pattern;
    }
    $sorted = $patterns;
    sort($sorted);
    ensure((string)$privilege->name === NAME && count($privilege->children()) === 2 && count($privilege->patterns->children()) === count(PATTERNS) && $sorted === PATTERNS, 'ACL source grants unexpected access');
    foreach ($xml->xpath('//*') as $element) {
        ensure(count($element->attributes()) === 0, 'ACL attributes are prohibited');
    }
    $entry = ['name' => (string)$privilege->name, 'match' => $patterns];
    $before = snapshot();
    $changes = drift($before, $content, $entry);
    if ($request['action'] === 'inspect') {
        echo json_encode(['state' => $before, 'fingerprint' => fingerprint($before), 'drift' => $changes], JSON_THROW_ON_ERROR) . PHP_EOL;
        exit(0);
    }
    ensure(($request['expected_fingerprint'] ?? '') === fingerprint($before), 'ACL state changed after planning; create a new plan');
    if (!$changes) {
        echo json_encode(['status' => 'pass', 'changed' => false, 'drift' => [], 'fingerprint' => fingerprint($before)], JSON_THROW_ON_ERROR) . PHP_EOL;
        exit(0);
    }
    $lockPath = LOCK;
    ensure(!is_link($lockPath) && (!file_exists($lockPath) || (is_file($lockPath) && fileowner($lockPath) === OWNER_UID)), 'Invalid ACL lock path');
    $lock = fopen($lockPath, 'c');
    ensure($lock !== false && flock($lock, LOCK_EX | LOCK_NB), 'Another ACL reconciliation is running');
    ensure(chmod($lockPath, 0600), 'Cannot protect ACL lock');
    ensure(fingerprint(snapshot()) === fingerprint($before), 'ACL state changed while acquiring the lock; create a new plan');
    $recoveryPath = saveRecovery($before, $content);
    try {
        foreach (directories() as $path) {
            if (!is_dir($path)) {
                ensure(mkdir($path, 0755), 'Cannot create ACL directory');
            }
            setMetadata($path, ['uid' => OWNER_UID, 'gid' => OWNER_GID, 'mode' => 0755]);
        }
        atomicWrite($content, ['uid' => OWNER_UID, 'gid' => OWNER_GID, 'mode' => 0644]);
        refreshCache();
        $after = snapshot();
        ensure(drift($after, $content, $entry) === [], 'ACL convergence verification failed');
    } catch (Throwable $error) {
        try {
            restore($before, $content);
        } catch (Throwable $rollbackError) {
            throw new RuntimeException('ACL apply failed and rollback requires operator recovery');
        }
        throw new RuntimeException('ACL apply failed; previous file and permissions restored');
    } finally {
        flock($lock, LOCK_UN);
        fclose($lock);
    }
    echo json_encode(['status' => 'pass', 'changed' => true, 'drift' => [], 'fingerprint' => fingerprint($after), 'recovery_file' => $recoveryPath], JSON_THROW_ON_ERROR) . PHP_EOL;
} catch (Throwable $error) {
    echo json_encode(['status' => 'failed', 'error' => $error->getMessage(), 'recovery_file' => $recoveryPath ?? null]) . PHP_EOL;
    exit(1);
}
