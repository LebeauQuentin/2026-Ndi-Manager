from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
from ctypes import c_char_p, c_int32, c_uint32, c_void_p
from pathlib import Path

import objc
import psutil
import Quartz
from Cocoa import (
    NSURL,
    NSAlert,
    NSAlertStyleInformational,
    NSApp,
    NSApplication,
    NSApplicationActivateIgnoringOtherApps,
    NSApplicationActivationPolicyRegular,
    NSButton,
    NSEventModifierFlagCommand,
    NSFont,
    NSImage,
    NSImageView,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSPasteboard,
    NSPopUpButton,
    NSRunningApplication,
    NSScrollView,
    NSSearchField,
    NSStringPboardType,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSTextAlignmentCenter,
    NSTimer,
    NSWindow,
    NSWorkspace,
)

# Active NDI par défaut sur les machines compatibles.
# Sur une machine qui segfault avec libndi.dylib, tu peux repasser localement à False.
ENABLE_NDI = True


def _get_macos_major_version() -> int:
    mac_ver, _, _ = platform.mac_ver()
    if not mac_ver:
        return 0
    try:
        return int(mac_ver.split(".")[0])
    except (ValueError, IndexError):
        return 0


def _validate_supported_platform_or_exit() -> None:
    system_name = platform.system().lower()
    machine = platform.machine().lower()
    major = _get_macos_major_version()

    # Cette application cible uniquement macOS 13+ sur Apple Silicon.
    if system_name != "darwin":
        sys.stderr.write(
            "NDI Manager supporte uniquement macOS 13+ sur Apple Silicon (puce M).\n"
        )
        raise SystemExit(1)

    if machine != "arm64":
        sys.stderr.write(
            "Architecture non supportee. Utilise un Mac Apple Silicon (arm64 / puce M).\n"
        )
        raise SystemExit(1)

    if major < 13:
        sys.stderr.write(
            "Version non supportee. NDI Manager requiert macOS 13 (Ventura) ou plus recent.\n"
        )
        raise SystemExit(1)


class NDIlib_source_t(ctypes.Structure):
    class _UrlOrIpUnion(ctypes.Union):
        _fields_ = [
            ("p_url_address", c_char_p),
            ("p_ip_address", c_char_p),
        ]

    _fields_ = [
        ("p_ndi_name", c_char_p),
        ("u", _UrlOrIpUnion),
    ]


class NDIlib_find_create_t(ctypes.Structure):
    _fields_ = [
        ("show_local_sources", ctypes.c_bool),
        ("p_groups", c_char_p),
        ("p_extra_ips", c_char_p),
    ]


class NDIlib_recv_create_v3_t(ctypes.Structure):
    _fields_ = [
        ("source_to_connect_to", NDIlib_source_t),
        ("color_format", c_int32),
        ("bandwidth", c_int32),
        ("allow_video_fields", ctypes.c_bool),
        ("p_ndi_recv_name", c_char_p),
    ]


class NDIlib_video_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("xres", c_int32),
        ("yres", c_int32),
        ("FourCC", c_uint32),
        ("frame_rate_N", c_int32),
        ("frame_rate_D", c_int32),
        ("picture_aspect_ratio", ctypes.c_float),
        ("frame_format_type", c_int32),
        ("timecode", ctypes.c_int64),
        ("p_data", ctypes.POINTER(ctypes.c_uint8)),
        ("line_stride_in_bytes", c_int32),
        ("p_metadata", c_char_p),
        ("timestamp", ctypes.c_int64),
    ]


class NDIWrapper:
    def __init__(self):
        # Mode compatibilité : on ne tente pas NDI si désactivé explicitement.
        if not ENABLE_NDI:
            raise RuntimeError("NDI désactivé (mode compatibilité forcé).")

        # Heuristique simple : on ne tente pas NDI sur macOS < 13 pour éviter
        # les plantages connus de certaines versions de libndi.dylib.
        mac_ver, _, _ = platform.mac_ver()
        major = _get_macos_major_version()
        if major and major < 13:
            raise RuntimeError(
                f"NDI non supporté sur macOS {mac_ver} (mode compatibilité activé, nécessite macOS 13+)."
            )

        ndi_dir = os.environ.get("NDI_SDK_DIR", "/Library/NDI SDK for Apple")
        lib_path = os.path.join(ndi_dir, "lib", "macOS", "libndi.dylib")
        if not os.path.exists(lib_path):
            raise RuntimeError(f"Impossible de trouver libndi.dylib à {lib_path}")

        self.lib = ctypes.cdll.LoadLibrary(lib_path)
        # Keep-alive des objets passés à NDI via ctypes.
        # On garde bytes + structures (source/settings) pour éviter toute invalidation
        # prématurée côté Python.
        self._ndi_keepalive: dict[int, dict] = {}

        # Initialisation NDI
        if not self.lib.NDIlib_initialize():
            raise RuntimeError("Échec de l'initialisation NDI")

        # Définition des signatures pour les fonctions utilisées
        self.lib.NDIlib_find_create_v2.restype = c_void_p
        self.lib.NDIlib_find_create_v2.argtypes = [ctypes.POINTER(NDIlib_find_create_t)]

        # NDI SDK: NDIlib_find_get_current_sources(finder, &no_sources)
        # (2 args) retourne un pointeur vers NDIlib_source_t const*.
        self.lib.NDIlib_find_get_current_sources.restype = ctypes.POINTER(NDIlib_source_t)
        self.lib.NDIlib_find_get_current_sources.argtypes = [
            c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]

        self.lib.NDIlib_find_destroy.restype = None
        self.lib.NDIlib_find_destroy.argtypes = [c_void_p]
        self.lib.NDIlib_find_wait_for_sources.restype = ctypes.c_bool
        self.lib.NDIlib_find_wait_for_sources.argtypes = [c_void_p, c_uint32]

        # Receiver
        self.lib.NDIlib_recv_create_v3.restype = c_void_p
        self.lib.NDIlib_recv_create_v3.argtypes = [ctypes.POINTER(NDIlib_recv_create_v3_t)]

        self.lib.NDIlib_recv_destroy.restype = None
        self.lib.NDIlib_recv_destroy.argtypes = [c_void_p]

        self.lib.NDIlib_recv_connect.restype = None
        self.lib.NDIlib_recv_connect.argtypes = [c_void_p, ctypes.POINTER(NDIlib_source_t)]

        self.lib.NDIlib_recv_capture_v2.restype = c_int32
        self.lib.NDIlib_recv_capture_v2.argtypes = [
            c_void_p,
            ctypes.POINTER(NDIlib_video_frame_v2_t),
            c_void_p,
            c_void_p,
            c_uint32,
        ]

        self.lib.NDIlib_recv_free_video_v2.restype = None
        self.lib.NDIlib_recv_free_video_v2.argtypes = [c_void_p, ctypes.POINTER(NDIlib_video_frame_v2_t)]

    def list_sources(self):
        create_desc = NDIlib_find_create_t(
            show_local_sources=True,
            p_groups=None,
            p_extra_ips=None,
        )
        finder = self.lib.NDIlib_find_create_v2(ctypes.byref(create_desc))
        if not finder:
            raise RuntimeError("Impossible de créer le finder NDI")

        try:
            # On laisse 2 secondes pour découvrir les sources
            time.sleep(2.0)

            no_sources = ctypes.c_uint32(0)
            sources_ptr = self.lib.NDIlib_find_get_current_sources(
                finder, ctypes.byref(no_sources)
            )
            count = int(no_sources.value or 0)
            if not sources_ptr or count <= 0:
                return []

            result = []
            # Les chaînes retournées dans NDIlib_source_t restent valides
            # jusqu'au prochain appel de NDIlib_find_get_current_sources ou destroy.
            for i in range(count):
                src = sources_ptr[i]
                name = src.p_ndi_name.decode("utf-8") if src.p_ndi_name else "Unnamed"
                raw_url = src.u.p_url_address.decode("utf-8") if src.u.p_url_address else None
                raw_ip = src.u.p_ip_address.decode("utf-8") if src.u.p_ip_address else None
                # Conserver url et ip séparément:
                # - 'url' affichée = p_url_address (si dispo)
                # - 'ip' affichée = IP extraite depuis url, sinon ip_raw
                # - 'ip_raw' (caché côté UI) = p_ip_address brut (utile pour create_receiver)
                url = raw_url or ""
                ip_from_url = _extract_ip_from_url(raw_url) if raw_url else None
                ip_display = ip_from_url or (raw_ip or "") or ""
                result.append({"name": name, "url": url, "ip": ip_display, "ip_raw": raw_ip or ""})
            return result
        finally:
            self.lib.NDIlib_find_destroy(finder)

    def create_receiver(self, source: dict, recv_name: str = "NDI Manager Preview"):
        source_name = source.get("name", "")
        recv_name_b = recv_name.encode("utf-8")

        # Chemin préféré: retrouver la source exacte via le finder puis créer le receiver
        # avec la struct NDIlib_source_t recopiée telle que fournie par le SDK.
        finder = None
        try:
            finder_desc = NDIlib_find_create_t(
                show_local_sources=True,
                p_groups=None,
                p_extra_ips=None,
            )
            finder = self.lib.NDIlib_find_create_v2(ctypes.byref(finder_desc))
            if finder:
                # Laisse au finder le temps de mettre à jour la table.
                self.lib.NDIlib_find_wait_for_sources(finder, 1000)
                no_sources = ctypes.c_uint32(0)
                sources_ptr = self.lib.NDIlib_find_get_current_sources(
                    finder, ctypes.byref(no_sources)
                )
                count = int(no_sources.value or 0)
                if sources_ptr and count > 0 and source_name:
                    for i in range(count):
                        s = sources_ptr[i]
                        n = s.p_ndi_name.decode("utf-8") if s.p_ndi_name else ""
                        if n == source_name:
                            # Copie "owned" des champs source pour ne pas dépendre
                            # de la mémoire interne du finder.
                            n_b = s.p_ndi_name if s.p_ndi_name else b""
                            u_b = s.u.p_url_address if s.u.p_url_address else b""
                            ip_b = s.u.p_ip_address if s.u.p_ip_address else b""

                            src_owned = NDIlib_source_t()
                            src_owned.p_ndi_name = n_b if n_b else None
                            if u_b:
                                src_owned.u.p_url_address = u_b
                                src_owned.u.p_ip_address = None
                            elif ip_b:
                                src_owned.u.p_ip_address = ip_b
                                src_owned.u.p_url_address = None

                            settings = NDIlib_recv_create_v3_t(
                                source_to_connect_to=src_owned,
                                color_format=0,
                                bandwidth=100,
                                allow_video_fields=True,
                                p_ndi_recv_name=recv_name_b,
                            )
                            inst = self.lib.NDIlib_recv_create_v3(ctypes.byref(settings))
                            if inst:
                                try:
                                    self.lib.NDIlib_recv_connect(inst, ctypes.byref(src_owned))
                                except Exception:
                                    pass
                                try:
                                    inst_key = int(inst)
                                    self._ndi_keepalive[inst_key] = {
                                        "bytes": tuple(
                                            b
                                            for b in (recv_name_b, n_b, u_b, ip_b)
                                            if b is not None and isinstance(b, (bytes, bytearray)) and len(b) > 0
                                        ),
                                        "source_struct": src_owned,
                                        "settings_struct": settings,
                                    }
                                except Exception:
                                    pass
                                return inst
                            break
        except Exception:
            # fallback: mode string-based ci-dessous
            pass
        finally:
            if finder:
                try:
                    self.lib.NDIlib_find_destroy(finder)
                except Exception:
                    pass

        ndi_name_b = source.get("name", "").encode("utf-8")
        url_raw = source.get("url") or ""
        ip_raw = source.get("ip_raw") or ""
        ip_display = source.get("ip") or ""

        url_b = url_raw.encode("utf-8") if url_raw else b""
        ip_raw_b = ip_raw.encode("utf-8") if ip_raw else b""
        ip_display_b = ip_display.encode("utf-8") if ip_display else b""

        src = NDIlib_source_t()
        src.p_ndi_name = ndi_name_b if ndi_name_b else None

        # Preferer p_url_address si disponible (peu importe la présence ou non d'un scheme).
        # Sinon utiliser p_ip_address brut si présent.
        if url_raw:
            src.u.p_url_address = url_b
            src.u.p_ip_address = None
        elif ip_raw:
            src.u.p_ip_address = ip_raw_b
            src.u.p_url_address = None
        elif ip_display:
            # fallback ultime (affiché) - mieux vaut moins de heuristique possible.
            src.u.p_ip_address = ip_display_b
            src.u.p_url_address = None

        # color_format = NDIlib_recv_color_format_BGRX_BGRA (0)
        # bandwidth = NDIlib_recv_bandwidth_highest (100)
        settings = NDIlib_recv_create_v3_t(
            source_to_connect_to=src,
            color_format=0,
            bandwidth=100,
            allow_video_fields=True,
            p_ndi_recv_name=recv_name_b,
        )
        inst = self.lib.NDIlib_recv_create_v3(ctypes.byref(settings))
        if not inst:
            raise RuntimeError("Impossible de créer le receiver NDI")

        # Force une connexion explicite au cas où la source initiale ne soit pas
        # prise en compte immédiatement par l'implémentation.
        try:
            self.lib.NDIlib_recv_connect(inst, ctypes.byref(src))
        except Exception:
            pass

        # Keep-alive jusqu'au destroy_receiver
        try:
            inst_key = int(inst)
            keep = tuple(
                b
                for b in (ndi_name_b, url_b, ip_raw_b, ip_display_b, recv_name_b)
                if b is not None and isinstance(b, (bytes, bytearray)) and len(b) > 0
            )
            self._ndi_keepalive[inst_key] = {
                "bytes": keep,
                "source_struct": src,
                "settings_struct": settings,
            }
        except Exception:
            pass

        return inst

    def capture_video_frame(self, recv_instance, timeout_ms: int = 1000):
        frame = NDIlib_video_frame_v2_t()
        frame_type = self.lib.NDIlib_recv_capture_v2(
            recv_instance, ctypes.byref(frame), None, None, timeout_ms
        )
        # 1 == video, 0 == none, 4 == error
        if frame_type != 1:
            return None, frame_type
        return frame, frame_type

    def free_video_frame(self, recv_instance, frame):
        self.lib.NDIlib_recv_free_video_v2(recv_instance, ctypes.byref(frame))

    def destroy_receiver(self, recv_instance):
        if recv_instance:
            try:
                self._ndi_keepalive.pop(int(recv_instance), None)
            except Exception:
                pass
            self.lib.NDIlib_recv_destroy(recv_instance)


def _extract_ip_from_url(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", url)
    return m.group(1) if m else None


def _app_support_dir() -> Path:
    d = Path.home() / ".ndi-manager"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_favorites() -> set[str]:
    p = _app_support_dir() / "favorites.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(str(x) for x in data)
        return set()
    except Exception:
        return set()


def _save_favorites(favs: set[str]) -> None:
    p = _app_support_dir() / "favorites.json"
    p.write_text(json.dumps(sorted(favs), indent=2), encoding="utf-8")


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

    # Heuristique: si on a une mesure par interface, on préfère celle-là.
    mbps_measured = mbps_iface if mbps_iface is not None else mbps_total
    ok = mbps_measured >= required_mbps

    mode_txt = "Total machine (tous les interfaces)" if not iface else f"Interface: {iface}"

    lines = []
    lines.append(f"Profil: {profile} (seuil recommandé: {required_mbps:.0f} Mbps)")
    lines.append(f"Mode: {mode_txt}")
    lines.append(f"Fenêtre de mesure: {duration_seconds:.0f}s (trafic actuel)")
    if link_mbps is not None:
        lines.append(
            f"Link speed déclaré: {link_mbps:.0f} Mbps"
            + ("" if isup is None else f" — {'UP' if isup else 'DOWN'}")
        )
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


def _ndi_frame_to_nsimage(frame: NDIlib_video_frame_v2_t):
    # On attend du BGRX/BGRA 8bpp pour une conversion simple.
    # FourCC pour BGRX = 'BGRX' et BGRA = 'BGRA'. On accepte les deux.
    fourcc_bgrx = (ord("B") | (ord("G") << 8) | (ord("R") << 16) | (ord("X") << 24))
    fourcc_bgra = (ord("B") | (ord("G") << 8) | (ord("R") << 16) | (ord("A") << 24))
    if frame.FourCC not in (fourcc_bgrx, fourcc_bgra):
        # Format non géré pour l'aperçu rapide
        return None

    w = int(frame.xres)
    h = int(frame.yres)
    if w <= 0 or h <= 0:
        return None

    # p_data peut être NULL si le flux est en erreur ou non initialisé
    if not bool(frame.p_data):
        return None

    stride = int(frame.line_stride_in_bytes) if int(frame.line_stride_in_bytes) else w * 4
    size = max(stride * h, 0)
    if size == 0:
        return None

    # Copie mémoire (on ne peut pas garder le pointeur après free)
    buf = ctypes.string_at(frame.p_data, size)

    # Créer un CGImage depuis BGRA/BGRX
    data = Quartz.CFDataCreate(None, buf, len(buf))
    if data is None:
        return None
    provider = Quartz.CGDataProviderCreateWithCFData(data)
    color_space = Quartz.CGColorSpaceCreateDeviceRGB()
    bitmap_info = Quartz.kCGBitmapByteOrder32Little | Quartz.kCGImageAlphaNoneSkipFirst
    if frame.FourCC == fourcc_bgra:
        bitmap_info = Quartz.kCGBitmapByteOrder32Little | Quartz.kCGImageAlphaPremultipliedFirst

    cgimg = Quartz.CGImageCreate(
        w,
        h,
        8,
        32,
        stride,
        color_space,
        bitmap_info,
        provider,
        None,
        False,
        Quartz.kCGRenderingIntentDefault,
    )
    if cgimg is None:
        return None
    return NSImage.alloc().initWithCGImage_size_(cgimg, (w, h))


class TableDataSource(NSObject):
    def initWithData_(self, data):
        self = objc.super(TableDataSource, self).init()
        if self is None:
            return None
        self.data = data or []
        self.filtered = self.data
        return self

    def numberOfRowsInTableView_(self, tableView):
        return len(self.filtered)

    def tableView_objectValueForTableColumn_row_(self, tableView, column, row):
        try:
            key = column.identifier()
            key_s = str(key) if key is not None else ""
            if row < 0 or row >= len(self.filtered):
                return ""
            v = self.filtered[row].get(key_s, "")
            return v if v is not None else ""
        except Exception:
            # Ne jamais lever d'exception dans un callback Cocoa: PyObjC peut sinon planter.
            return ""

    def updateData_(self, new_data):
        self.data = new_data or []
        self.filtered = self.data

    def applyFilter_favorites_(self, query: str, favorites: set[str]):
        q = (query or "").strip().lower()
        rows = self.data
        if q:
            rows = [
                r
                for r in rows
                if q in (r.get("name", "").lower())
                or q in (r.get("ip", "").lower())
                or q in (r.get("url", "").lower())
            ]
        # Favoris en haut
        def _key(r):
            return (0 if r.get("name", "") in favorites else 1, r.get("name", ""))

        self.filtered = sorted(rows, key=_key)

    def rowAt_(self, idx: int):
        if idx < 0 or idx >= len(self.filtered):
            return None
        return self.filtered[idx]


class PreviewController(NSObject):
    def initWithNDI_source_(self, ndi: NDIWrapper, source: dict):
        self = objc.super(PreviewController, self).init()
        if self is None:
            return None
        self.ndi = ndi
        self.source = source
        self.recv = None
        self.timer = None
        self.window = None
        self.image_view = None
        self._initial_image = None
        return self

    def initWithNDI_source_receiver_image_(
        self, ndi: NDIWrapper, source: dict, recv, image
    ):
        self = objc.super(PreviewController, self).init()
        if self is None:
            return None
        self.ndi = ndi
        self.source = source
        self.recv = recv
        self.timer = None
        self.window = None
        self.image_view = None
        self._initial_image = image
        return self

    def show(self):
        w, h = 960, 540
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, w, h), 15, 2, False
        )
        self.window.setTitle_(f"Preview — {self.source.get('name','')}")

        self.image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        self.image_view.setImageScaling_(3)  # NSImageScaleProportionallyUpOrDown
        self.window.contentView().addSubview_(self.image_view)

        # Afficher immédiatement la première image si on en a une.
        if self._initial_image is not None:
            try:
                self.image_view.setImage_(self._initial_image)
            except Exception:
                pass

        self.window.makeKeyAndOrderFront_(None)

        if self.recv is None:
            self.recv = self.ndi.create_receiver(self.source)

        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / 15.0, self, "tick:", None, True
        )

    def close(self):
        if self.timer:
            self.timer.invalidate()
            self.timer = None
        if self.recv:
            self.ndi.destroy_receiver(self.recv)
            self.recv = None
        if self.window:
            self.window.close()
            self.window = None

    def tick_(self, timer):
        if not self.recv:
            return
        # Évite un spin trop agressif: timeout non nul + intervalle plus bas.
        frame, frame_type = self.ndi.capture_video_frame(self.recv, timeout_ms=50)
        if frame is None:
            return
        try:
            img = _ndi_frame_to_nsimage(frame)
            if img is not None:
                self.image_view.setImage_(img)
        finally:
            self.ndi.free_video_frame(self.recv, frame)


class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self._alert_last_shown = {}
        self._did_warn_ndi_unavailable = False

        screen_frame = NSApp().mainWindow().screen().frame() if NSApp().mainWindow() else None
        # Fenêtre principale.
        width, height = 960, 480
        x = 100
        y = 100
        if screen_frame is not None:
            x = (screen_frame.size.width - width) / 2
            y = (screen_frame.size.height - height) / 2

        # Fenêtre non redimensionnable pour garder un layout propre
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, width, height),
            7,  # titled, closable, miniaturizable
            2,  # buffered
            False,
        )
        self.window.setTitle_("NDI Manager")

        # NDI wrapper
        try:
            self.ndi = NDIWrapper()
        except Exception as e:
            # Message de démarrage clair + lien vers le site NDI.
            msg = (
                f"{e}\n\n"
                "Pour activer la découverte et le preview NDI sur les machines compatibles :\n"
                "- Installe NDI SDK / NDI Tools pour macOS 13+.\n"
                "- Redémarre ensuite l'application.\n"
            )

            alert = NSAlert.alloc().init()
            alert.setMessageText_("Erreur NDI / Mode compatibilité")
            alert.setInformativeText_(msg)
            alert.setAlertStyle_(NSAlertStyleInformational)
            alert.addButtonWithTitle_("Ouvrir la page NDI")
            alert.addButtonWithTitle_("OK")
            response = alert.runModal()
            # Premier bouton : ouvrir la page de téléchargement du NDI SDK (développeurs)
            if response == 1000:  # NSAlertFirstButtonReturn
                self._open_url("https://ndi.video/for-developers/ndi-sdk/")
            self.ndi = None

        # Table des sources
        # Zone centrale entre la barre du haut (recherche/interfaces) et les boutons du bas.
        table_top = height - 120
        table_bottom = 70
        table_height = table_top - table_bottom

        # Laisser de la place à droite pour un mini-preview
        table_width = width - 260
        self.table_view = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, table_width - 20, table_height))

        col_name = NSTableColumn.alloc().initWithIdentifier_("name")
        col_name.setWidth_(250)
        col_name.headerCell().setStringValue_("Source")

        col_ip = NSTableColumn.alloc().initWithIdentifier_("ip")
        col_ip.setWidth_(150)
        col_ip.headerCell().setStringValue_("IP")

        col_url = NSTableColumn.alloc().initWithIdentifier_("url")
        col_url.setWidth_(200)
        col_url.headerCell().setStringValue_("URL")

        self.table_view.addTableColumn_(col_name)
        self.table_view.addTableColumn_(col_ip)
        self.table_view.addTableColumn_(col_url)

        self.data_source = TableDataSource.alloc().initWithData_([])
        self.table_view.setDataSource_(self.data_source)

        scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, table_bottom, table_width - 20, table_height))
        scroll_view.setDocumentView_(self.table_view)
        scroll_view.setHasVerticalScroller_(True)

        # Mini preview à droite
        # (zone reservée au bloc "Check Network" pour une meilleure séparation visuelle)
        preview_offset_y = 140
        preview_height = max(1, table_height - preview_offset_y)
        self.preview_image = NSImageView.alloc().initWithFrame_(
            NSMakeRect(table_width, table_bottom + preview_offset_y, 220, preview_height)
        )
        self.preview_image.setImageScaling_(3)  # proportionnel

        # Search
        self.search_field = NSSearchField.alloc().initWithFrame_(NSMakeRect(20, height - 55, 260, 22))
        self.search_field.setPlaceholderString_("Filtrer (nom / IP / URL)")
        self.search_field.setTarget_(self)
        self.search_field.setAction_("searchChanged:")

        # Network interface & profile pickers (groupés avec Check Network, en bas)
        self.iface_picker = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(160, 52, 180, 22))
        self._populate_interfaces()

        self.profile_picker = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(350, 52, 160, 22))
        self.profile_picker.addItemsWithTitles_(["NDI 720p", "NDI 1080p", "NDI 1080p (safe)", "NDI 4K"])

        # Bouton Info (IP / interfaces / NDI) - petit bouton "ⓘ" à droite
        self.info_button = NSButton.alloc().initWithFrame_(NSMakeRect(width - 70, height - 57, 40, 24))
        self.info_button.setTitle_("ⓘ")
        self.info_button.setBezelStyle_(2)  # NSRoundedBezelStyle
        self.info_button.setBordered_(True)
        self.info_button.setTarget_(self)
        self.info_button.setAction_("showInfo:")

        # Status, juste sous la barre du haut
        self.status_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, height - 95, width - 40, 18))
        self.status_label.setEditable_(False)
        self.status_label.setBordered_(False)
        self.status_label.setDrawsBackground_(False)
        self.status_label.setStringValue_("Prêt. EasyIP SetupTool Plus est uniquement disponible sous Windows (PC ou VM).")

        # Bloc "Network test" clair à droite du tableau
        right_panel_x = table_width
        right_panel_w = width - table_width
        network_x = right_panel_x + 10
        network_w = right_panel_w - 20

        self.network_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(network_x, 180, network_w, 20)
        )
        self.network_label.setEditable_(False)
        # Style discret : header de section, pas une "box" visuelle épaisse.
        self.network_label.setBordered_(False)
        self.network_label.setDrawsBackground_(False)
        self.network_label.setBezeled_(False)
        self.network_label.setFont_(NSFont.boldSystemFontOfSize_(14.0))
        self.network_label.setAlignment_(NSTextAlignmentCenter)
        self.network_label.setStringValue_("Network test")

        self.check_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(network_x, 88, network_w, 24)
        )
        self.check_button.setTitle_("Check Network")
        self.check_button.setTarget_(self)
        self.check_button.setAction_("checkNetwork:")

        # Pickers dans la zone Network test (empile verticalement)
        # AppKit: coordonnee Y depuis le bas -> label en premier (plus haut), puis selects, puis bouton.
        self.iface_picker.setFrame_(NSMakeRect(network_x, 150, network_w, 22))
        self.profile_picker.setFrame_(NSMakeRect(network_x, 120, network_w, 22))

        # Ligne de boutons bas (centrée sur les actions courantes)
        self.refresh_button = NSButton.alloc().initWithFrame_(NSMakeRect(20, 15, 130, 26))
        self.refresh_button.setTitle_("Refresh NDI")
        self.refresh_button.setTarget_(self)
        self.refresh_button.setAction_("manualRefreshNDI:")

        self.preview_button = NSButton.alloc().initWithFrame_(NSMakeRect(160, 15, 90, 26))
        self.preview_button.setTitle_("Preview")
        self.preview_button.setTarget_(self)
        self.preview_button.setAction_("previewSelected:")

        # Copy buttons
        self.copy_ip_button = NSButton.alloc().initWithFrame_(NSMakeRect(260, 15, 80, 26))
        self.copy_ip_button.setTitle_("Copy IP")
        self.copy_ip_button.setTarget_(self)
        self.copy_ip_button.setAction_("copyIP:")

        self.copy_url_button = NSButton.alloc().initWithFrame_(NSMakeRect(350, 15, 90, 26))
        self.copy_url_button.setTitle_("Copy URL")
        self.copy_url_button.setTarget_(self)
        self.copy_url_button.setAction_("copyURL:")

        self.ping_button = NSButton.alloc().initWithFrame_(NSMakeRect(450, 15, 70, 26))
        self.ping_button.setTitle_("Ping")
        self.ping_button.setTarget_(self)
        self.ping_button.setAction_("ping:")
        # Plus de bouton Favorite dédié ni Open Web UI en bas : actions regroupées dans le menu Tools.

        # Menu Tools (Open Web UI / NDI Tools / Panasonic) en haut à droite
        self.tools_picker = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(width - 260, height - 57, 180, 24))
        self.tools_picker.addItemsWithTitles_([
            "Tools…",
            "Open Web UI (selected)",
            "Open NDI Tools",
            "Open Panasonic EasyIP page",
        ])
        self.tools_picker.setTarget_(self)
        self.tools_picker.setAction_("toolsAction:")

        content_view = self.window.contentView()
        content_view.addSubview_(self.search_field)
        content_view.addSubview_(self.tools_picker)
        content_view.addSubview_(self.info_button)
        content_view.addSubview_(self.iface_picker)
        content_view.addSubview_(self.profile_picker)
        content_view.addSubview_(self.status_label)
        content_view.addSubview_(self.network_label)
        content_view.addSubview_(scroll_view)
        content_view.addSubview_(self.preview_image)
        content_view.addSubview_(self.check_button)
        content_view.addSubview_(self.refresh_button)
        content_view.addSubview_(self.preview_button)
        content_view.addSubview_(self.copy_ip_button)
        content_view.addSubview_(self.copy_url_button)
        content_view.addSubview_(self.ping_button)

        self.window.makeKeyAndOrderFront_(None)

        # Première découverte
        self.favorites = _load_favorites()
        self.preview_controller = None
        self.last_source_names = set()
        self.refreshNDI_(None)

        # Auto-refresh léger
        self.refresh_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            10.0, self, "refreshNDI:", None, True
        )

    def refreshNDI_(self, sender):
        self._refresh_ndi(show_popup_on_error=False)

    def manualRefreshNDI_(self, sender):
        self._refresh_ndi(show_popup_on_error=True)

    def _refresh_ndi(self, show_popup_on_error: bool):
        if not self.ndi:
            self.status_label.setStringValue_("NDI non initialisé (mode compatibilité).")
            if show_popup_on_error and not self._did_warn_ndi_unavailable:
                self._show_alert(
                    "NDI",
                    "NDI n'est pas initialisé (mode compatibilité, voir message de démarrage).",
                )
                self._did_warn_ndi_unavailable = True
            return
        try:
            self.status_label.setStringValue_("Recherche des sources NDI…")
            sources = self.ndi.list_sources()
            self._did_warn_ndi_unavailable = False
            names = set(s.get("name", "") for s in sources if s.get("name"))
            appeared = sorted(names - self.last_source_names)
            disappeared = sorted(self.last_source_names - names)
            self.last_source_names = names

            self.data_source.updateData_(sources)
            self.data_source.applyFilter_favorites_(self.search_field.stringValue(), self.favorites)
            self.table_view.reloadData()
            self.status_label.setStringValue_(
                f"{len(sources)} source(s) NDI détectée(s). Dernier scan: {time.strftime('%H:%M:%S')}"
            )
            if appeared or disappeared:
                delta = []
                if appeared:
                    delta.append(f"+{len(appeared)}")
                if disappeared:
                    delta.append(f"-{len(disappeared)}")
                self.status_label.setStringValue_(
                    f"{len(sources)} source(s) NDI ({', '.join(delta)}). Dernier scan: {time.strftime('%H:%M:%S')}"
                )
        except Exception as e:
            if show_popup_on_error:
                self._show_alert_throttled("ndi-refresh", "Erreur NDI", str(e), cooldown_s=20.0)
            self.status_label.setStringValue_("Erreur pendant le scan NDI.")

    def checkNetwork_(self, sender):
        self.status_label.setStringValue_("Mesure du réseau (3s)…")
        mode = self.iface_picker.titleOfSelectedItem()
        profile = self.profile_picker.titleOfSelectedItem()

        iface_arg: str | None = None
        if not mode:
            iface_arg = None
        elif mode.startswith("(Total"):
            iface_arg = None
        elif mode.startswith("(Auto"):
            iface_arg = self._pick_auto_interface()
        else:
            # Interface nommée
            iface_arg = mode

        ok, report = check_network_report(3.0, profile, iface_arg)
        self._show_alert("Check Network", report)
        self.status_label.setStringValue_("Check Network terminé." + (" OK" if ok else " KO"))

    def searchChanged_(self, sender):
        self.data_source.applyFilter_favorites_(self.search_field.stringValue(), self.favorites)
        self.table_view.reloadData()

    def _selected_row(self):
        idx = self.table_view.selectedRow()
        return self.data_source.rowAt_(idx)

    def previewSelected_(self, sender):
        if not self.ndi:
            self._show_alert("Preview", "NDI n'est pas initialisé.")
            return

        row = self._selected_row()
        if not row:
            self._show_alert("Preview", "Sélectionne une source NDI d'abord.")
            return

        # Pré-test de stabilité : on n'ouvre la boucle temps réel que si on reçoit
        # effectivement au moins une frame vidéo. (Certaines sources peuvent sinon
        # provoquer des crashs dans la boucle NSTimer.)
        recv = None
        first_img = None
        got_video_frame = False
        try:
            if self.preview_controller is not None:
                try:
                    self.preview_controller.close()
                except Exception:
                    pass

            recv = self.ndi.create_receiver(row, recv_name="NDI Manager PreviewWarmup")

            # Warmup: on attend une frame vidéo réelle (frame_type == 1).
            for _ in range(10):
                frame, frame_type = self.ndi.capture_video_frame(recv, timeout_ms=400)
                if frame is None:
                    continue
                # frame est non-None => frame_type devrait être 1 dans notre wrapper
                got_video_frame = True
                try:
                    first_img = _ndi_frame_to_nsimage(frame)
                except Exception:
                    first_img = None
                finally:
                    try:
                        self.ndi.free_video_frame(recv, frame)
                    except Exception:
                        pass
                break

            if not got_video_frame:
                self._show_alert(
                    "Preview",
                    "Aucune frame vidéo n'a été reçue pour cette source.\n"
                    "La fenêtre temps réel n'est pas ouverte pour éviter un crash.",
                )
                return

            # Ouvrir la fenêtre temps réel avec le receiver déjà créé.
            self.preview_controller = PreviewController.alloc().initWithNDI_source_receiver_image_(
                self.ndi, row, recv, first_img
            )
            recv = None  # le contrôleur devient propriétaire
            if self.preview_controller is None:
                raise RuntimeError("PreviewController init a échoué.")
            self.preview_controller.show()
            self.status_label.setStringValue_(
                f"Preview temps réel ouvert pour: {row.get('name','')}"
            )
        except Exception as e:
            self._show_alert("Preview", f"Impossible d'ouvrir le preview temps réel.\n{e}")
        finally:
            if recv is not None:
                try:
                    self.ndi.destroy_receiver(recv)
                except Exception:
                    pass

    def copyIP_(self, sender):
        row = self._selected_row()
        if not row or not row.get("ip"):
            self._show_alert("Copy IP", "Aucune IP disponible pour cette source.")
            return
        self._copy_to_clipboard(row["ip"])
        self.status_label.setStringValue_(f"IP copiée: {row['ip']}")

    def copyURL_(self, sender):
        row = self._selected_row()
        if not row or not row.get("url"):
            self._show_alert("Copy URL", "Aucune URL disponible pour cette source.")
            return
        self._copy_to_clipboard(row["url"])
        self.status_label.setStringValue_("URL copiée.")

    def ping_(self, sender):
        row = self._selected_row()
        ip = row.get("ip") if row else None
        if not ip:
            self._show_alert("Ping", "Sélectionne une source avec une IP.")
            return

        self.status_label.setStringValue_(f"Ping {ip}…")

        def _run():
            try:
                p = subprocess.run(
                    ["ping", "-c", "3", "-W", "1000", ip],
                    capture_output=True,
                    text=True,
                )
                out = p.stdout.strip() or p.stderr.strip() or "(pas de sortie)"
                title = f"Ping {ip} — {'OK' if p.returncode == 0 else 'KO'}"
                self._show_alert_on_main_thread(title, out[:4000])
            finally:
                self._set_status_on_main_thread("Ping terminé.")

        threading.Thread(target=_run, daemon=True).start()

    def showInfo_(self, sender):
        # Récupère les infos réseau locales principales.
        hostname = socket.gethostname()

        if_addrs = psutil.net_if_addrs()
        if_stats = psutil.net_if_stats()

        # IP "principale" = première IPv4 non loopback de l'interface sélectionnée,
        # ou à défaut de en0, sinon la première trouvée.
        primary_ip = "inconnue"
        preferred_iface = self.iface_picker.titleOfSelectedItem() or ""
        candidates = []
        if preferred_iface and preferred_iface in if_addrs:
            candidates.append(preferred_iface)
        if "en0" in if_addrs and "en0" not in candidates:
            candidates.append("en0")
        for name in if_addrs.keys():
            if name not in candidates:
                candidates.append(name)

        for name in candidates:
            addrs = if_addrs.get(name, [])
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127."):
                    primary_ip = a.address
                    break
            if primary_ip != "inconnue":
                break

        lines = []
        lines.append(f"Hostname: {hostname}")
        lines.append(f"IP principale (cette machine): {primary_ip}")
        lines.append("")
        lines.append("Interfaces réseau :")

        for name, addrs in if_addrs.items():
            stats = if_stats.get(name)
            ips = [a.address for a in addrs if a.family == socket.AF_INET]
            if not ips:
                continue
            up_down = "UP" if stats and stats.isup else "DOWN"
            speed = f"{stats.speed} Mbps" if stats and stats.speed else "?"
            lines.append(f"- {name}: {', '.join(ips)}  [{up_down}, {speed}]")

        lines.append("")
        lines.append(f"NDI SDK dir: {os.environ.get('NDI_SDK_DIR', '/Library/NDI SDK for Apple')}")

        self._show_alert("Infos réseau (machine locale)", "\n".join(lines))

    def openWebUI_(self, sender):
        row = self._selected_row()
        ip = row.get("ip") if row else None
        if not ip:
            self._show_alert("Web UI", "Pas d'IP disponible pour cette source.")
            return
        self._open_url(f"http://{ip}")
        self.status_label.setStringValue_(f"Ouverture Web UI sur http://{ip}")

    def _copy_to_clipboard(self, s: str):
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(s, NSStringPboardType)

    def _populate_interfaces(self):
        self.iface_picker.removeAllItems()
        self.iface_picker.addItemWithTitle_("(Auto interface)")
        self.iface_picker.addItemWithTitle_("(Total machine)")

        ifaces = sorted(psutil.net_if_stats().keys())
        if not ifaces:
            self.iface_picker.addItemWithTitle_("(no interfaces)")
            return

        for i in ifaces:
            self.iface_picker.addItemWithTitle_(i)

    def _pick_auto_interface(self) -> str | None:
        stats = psutil.net_if_stats()
        if not stats:
            return None
        # Priorité: en0 si UP, sinon première interface UP non-loopback.
        st = stats.get("en0")
        if st and st.isup:
            return "en0"

        # Essayons dans un ordre déterministe
        for name in sorted(stats.keys()):
            if name.startswith("lo") or name.startswith("Loopback"):
                continue
            s = stats.get(name)
            if s and s.isup:
                return name
        return None

    def openNDITools_(self, sender=None):
        # Tentative d'ouverture d'une app NDI Tools si présente, sinon page de download
        candidates = [
            "/Applications/NDI Tools.app",
            "/Applications/NDI 6 Tools.app",
            "/Applications/NDI 5 Tools.app",
        ]
        for app_path in candidates:
            if os.path.exists(app_path):
                NSWorkspace.sharedWorkspace().openFile_(app_path)
                return
        self._open_url("https://ndi.video/tools/")

    def openPanasonicTool_(self, sender=None):
        # Ouvre directement la page de téléchargement EasyIP SetupTool Plus
        self._open_url("https://eww.pass.panasonic.co.jp/pro-av/support/content/download/EN/ep2main/easyIPplus_li_e.htm")

    def toolsAction_(self, sender):
        title = self.tools_picker.titleOfSelectedItem()
        try:
            if title == "Open Web UI (selected)":
                self.openWebUI_(None)
            elif title == "Open NDI Tools":
                self.openNDITools_(None)
            elif title == "Open Panasonic EasyIP page":
                self.openPanasonicTool_(None)
        finally:
            self.tools_picker.selectItemAtIndex_(0)

    def _open_url(self, url_str: str):
        url = NSURL.URLWithString_(url_str)
        if url is None:
            self._show_alert("Erreur", f"URL invalide: {url_str}")
            return
        NSWorkspace.sharedWorkspace().openURL_(url)

    def _show_alert(self, title, message):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.setAlertStyle_(NSAlertStyleInformational)
        alert.runModal()

    def _show_alert_throttled(self, key: str, title: str, message: str, cooldown_s: float = 10.0):
        now = time.time()
        last = self._alert_last_shown.get(key, 0.0)
        if (now - last) < cooldown_s:
            return
        self._alert_last_shown[key] = now
        self._show_alert(title, message)

    def _show_alert_on_main_thread(self, title: str, message: str):
        # Evite d'afficher un NSAlert depuis un thread secondaire.
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "showAlertFromPayload:",
            {"title": title, "message": message},
            False,
        )

    def showAlertFromPayload_(self, payload):
        title = payload.get("title", "Information")
        message = payload.get("message", "")
        self._show_alert(title, message)

    def _set_status_on_main_thread(self, text: str):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "setStatusFromPayload:",
            {"text": text},
            False,
        )

    def setStatusFromPayload_(self, payload):
        self.status_label.setStringValue_(payload.get("text", ""))

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        return True


def _install_macos_app_menu(app):
    main_menu = NSMenu.alloc().initWithTitle_("MainMenu")

    app_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
    main_menu.addItem_(app_menu_item)

    app_menu = NSMenu.alloc().initWithTitle_("Application")
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit NDI Manager",
        "terminate:",
        "q",
    )
    quit_item.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
    app_menu.addItem_(quit_item)
    app_menu_item.setSubmenu_(app_menu)

    app.setMainMenu_(main_menu)


def main():
    app = NSApplication.sharedApplication()
    _install_macos_app_menu(app)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    current_app = NSRunningApplication.currentApplication()
    current_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

    app.run()


if __name__ == "__main__":
    try:
        _validate_supported_platform_or_exit()
        main()
    except KeyboardInterrupt:
        sys.exit(0)

