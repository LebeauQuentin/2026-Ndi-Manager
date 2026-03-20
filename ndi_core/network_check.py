from __future__ import annotations

import time

import psutil


def _iface_is_loopback(iface: str) -> bool:
    try:
        addrs = psutil.net_if_addrs().get(iface, []) or []
    except Exception:
        return False

    for addr in addrs:
        a = getattr(addr, "address", "") or ""
        # Cas typique: 127.0.0.1
        if a.startswith("127."):
            return True
        # Certains systèmes exposent isloopback.
        if hasattr(addr, "isloopback") and addr.isloopback:
            return True
        # Et/ou une adresse qui ressemble à localhost.
        if a == "localhost":
            return True
    return False


def pick_auto_interface() -> str | None:
    """
    Choisit une interface "probable" pour tester le débit :
    - isup
    - non-loopback
    - première trouvée
    """

    try:
        stats = psutil.net_if_stats()
    except Exception:
        return None

    for iface, st in stats.items():
        try:
            if not st or not getattr(st, "isup", False):
                continue
            if _iface_is_loopback(iface):
                continue
            return iface
        except Exception:
            continue
    return None


def list_interfaces() -> list[str]:
    try:
        stats = psutil.net_if_stats()
    except Exception:
        return []

    out: list[str] = []
    for iface, st in stats.items():
        try:
            if not st or not getattr(st, "isup", False):
                continue
            if _iface_is_loopback(iface):
                continue
            out.append(iface)
        except Exception:
            continue
    return sorted(out)


def check_network_for_ndi_1080p(duration_seconds: float = 3.0):
    """
    Mesure le débit réseau moyen pendant quelques secondes et
    estime si suffisant pour un flux NDI 1080p (~150-200 Mbps).
    """

    required_mbps = 200.0

    counters_before = psutil.net_io_counters()
    bytes_before = counters_before.bytes_recv + counters_before.bytes_sent

    time.sleep(duration_seconds)

    counters_after = psutil.net_io_counters()
    bytes_after = counters_after.bytes_recv + counters_after.bytes_sent

    bytes_diff = bytes_after - bytes_before
    bits = bytes_diff * 8
    mbits = bits / 1_000_000.0
    mbps = mbits / duration_seconds

    ok = mbps >= required_mbps
    return ok, mbps, required_mbps


def check_network_report(duration_seconds: float, profile: str, iface: str | None):
    thresholds = {
        "NDI 720p": 120.0,
        "NDI 1080p": 200.0,
        "NDI 1080p (safe)": 250.0,
        "NDI 4K": 500.0,
    }
    required_mbps = thresholds.get(profile, 200.0)

    stats = psutil.net_if_stats()
    st = stats.get(iface) if iface else None
    link_mbps = float(st.speed) if st and st.speed else None
    isup = bool(st.isup) if st else None

    before = psutil.net_io_counters(pernic=True) if iface else None
    total_before = psutil.net_io_counters()
    bytes_before = total_before.bytes_recv + total_before.bytes_sent

    time.sleep(duration_seconds)

    total_after = psutil.net_io_counters()
    bytes_after = total_after.bytes_recv + total_after.bytes_sent
    bytes_diff = bytes_after - bytes_before
    mbps_total = (bytes_diff * 8) / 1_000_000.0 / duration_seconds

    mbps_iface = None
    if iface and before:
        after = psutil.net_io_counters(pernic=True)
        if iface in before and iface in after:
            b0 = before[iface].bytes_recv + before[iface].bytes_sent
            b1 = after[iface].bytes_recv + after[iface].bytes_sent
            mbps_iface = ((b1 - b0) * 8) / 1_000_000.0 / duration_seconds

    # Heuristique : si on a une mesure par interface, on préfère celle-là.
    mbps_measured = mbps_iface if mbps_iface is not None else mbps_total
    ok = mbps_measured >= required_mbps

    mode_txt = "Total machine (tous les interfaces)" if not iface else f"Interface: {iface}"

    lines = []
    lines.append(f"Profil: {profile} (seuil recommandé: {required_mbps:.0f} Mbps)")
    lines.append(f"Mode: {mode_txt}")
    lines.append(f"Fenêtre de mesure: {duration_seconds:.0f}s (trafic actuel)")
    if link_mbps is not None:
        lines.append(f"Link speed déclaré: {link_mbps:.0f} Mbps" + ("" if isup is None else f" — {'UP' if isup else 'DOWN'}"))
    lines.append(f"Débit observé: {mbps_measured:.1f} Mbps")
    if mbps_iface is not None:
        lines.append(f"(Total machine sur la même période: {mbps_total:.1f} Mbps)")
    lines.append("")

    if ok:
        lines.append("Résultat: OK (marge a priori suffisante pendant cette fenêtre).")
    else:
        lines.append("Résultat: KO (risque de saccades / drops pendant cette fenêtre).")
        lines.append(
            "Note: ce test mesure le trafic actuel (pas la capacité max). "
            "S'il n'y a presque aucun trafic sur la période, la mesure peut être basse."
        )

    return ok, "\n".join(lines)

