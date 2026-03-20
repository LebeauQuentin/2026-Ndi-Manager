from __future__ import annotations

import ctypes
import os
import platform
import re
import sys
import time
from ctypes import c_char_p, c_int32, c_uint32, c_void_p
from typing import Any

#
# NDI wrapper (ctypes) usable sur macOS + Windows.
#

ENABLE_NDI = True


def _get_macos_major_version() -> int:
    mac_ver, _, _ = platform.mac_ver()
    if not mac_ver:
        return 0
    try:
        return int(mac_ver.split(".")[0])
    except (ValueError, IndexError):
        return 0


def _extract_ip_from_url(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", url)
    return m.group(1) if m else None


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


def _find_windows_ndi_dll(dll_name: str) -> str | None:
    """
    Cherche la DLL NDI à charger.

    Ordre de préférence :
    - dossier PyInstaller (_MEIPASS) puis dossier de l'exe
    - variables d'environnement (NDILIB_REDIST_FOLDER / NDI_RUNTIME_DIR / NDI_REDIST_DIR)
    - emplacements par défaut Program Files
    """

    candidates: list[str] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(meipass)

    if getattr(sys, "executable", None):
        candidates.append(os.path.dirname(sys.executable))

    for env_key in ("NDILIB_REDIST_FOLDER", "NDI_RUNTIME_DIR", "NDI_REDIST_DIR", "NDI_SDK_DIR"):
        p = os.environ.get(env_key)
        if p:
            candidates.append(p)

    # Emplacements usuels NDI Runtime (Windows)
    candidates.extend(
        [
            r"C:\Program Files\NDI\NDI 5 Runtime",
            r"C:\Program Files (x86)\NDI\NDI 5 Runtime",
        ]
    )

    # Dedup en préservant l'ordre
    seen: set[str] = set()
    uniq: list[str] = []
    for c in candidates:
        if c not in seen:
            uniq.append(c)
            seen.add(c)

    for base in uniq:
        p = os.path.join(base, dll_name)
        if os.path.exists(p):
            return p

    # Si les DLL sont déjà dans le PATH / dossier système, on tente le nom brut.
    return None


class NDIWrapper:
    def __init__(self, *, enable_ndi: bool = ENABLE_NDI):
        if not enable_ndi:
            raise RuntimeError("NDI désactivé (enable_ndi=False).")

        system = platform.system().lower()
        if system == "darwin":
            # Heuristique : macOS < 13 -> risque de crash (comme ton setup actuel)
            major = _get_macos_major_version()
            if major and major < 13:
                raise RuntimeError(
                    f"NDI non supporté sur macOS {major} (mode compatibilité activé, nécessite macOS 13+)."
                )

        self._ndi_keepalive: dict[int, dict[str, Any]] = {}

        if system == "darwin":
            ndi_dir = os.environ.get("NDI_SDK_DIR", "/Library/NDI SDK for Apple")
            lib_path = os.path.join(ndi_dir, "lib", "macOS", "libndi.dylib")
            if not os.path.exists(lib_path):
                raise RuntimeError(f"Impossible de trouver libndi.dylib à {lib_path}")
            self.lib = ctypes.cdll.LoadLibrary(lib_path)
        elif system == "windows":
            # NDI SDK (Windows) : Processing.NDI.Lib.x64.dll / x86.dll
            dll_name = (
                "Processing.NDI.Lib.x64.dll"
                if ctypes.sizeof(ctypes.c_void_p) == 8
                else "Processing.NDI.Lib.x86.dll"
            )
            found = _find_windows_ndi_dll(dll_name)
            if found:
                self.lib = ctypes.cdll.LoadLibrary(found)
            else:
                # Laisse LoadLibrary gérer (PATH / dossier exécutable).
                self.lib = ctypes.cdll.LoadLibrary(dll_name)
        else:
            raise RuntimeError(f"NDIWrapper non supporté sur {system}")

        # Initialisation NDI
        if not self.lib.NDIlib_initialize():
            raise RuntimeError("Échec de l'initialisation NDI")

        # Définition signatures (macOS et Windows -> mêmes conventions pour les fonctions utilisées)
        self.lib.NDIlib_find_create_v2.restype = c_void_p
        self.lib.NDIlib_find_create_v2.argtypes = [ctypes.POINTER(NDIlib_find_create_t)]

        # NDIlib_find_get_current_sources(finder, &no_sources)
        self.lib.NDIlib_find_get_current_sources.restype = ctypes.POINTER(NDIlib_source_t)
        self.lib.NDIlib_find_get_current_sources.argtypes = [
            c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]

        self.lib.NDIlib_find_destroy.restype = None
        self.lib.NDIlib_find_destroy.argtypes = [c_void_p]

        self.lib.NDIlib_find_wait_for_sources.restype = ctypes.c_bool
        self.lib.NDIlib_find_wait_for_sources.argtypes = [c_void_p, c_uint32]

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
        self.lib.NDIlib_recv_free_video_v2.argtypes = [
            c_void_p,
            ctypes.POINTER(NDIlib_video_frame_v2_t),
        ]

    def list_sources(self) -> list[dict[str, str]]:
        create_desc = NDIlib_find_create_t(
            show_local_sources=True,
            p_groups=None,
            p_extra_ips=None,
        )
        finder = self.lib.NDIlib_find_create_v2(ctypes.byref(create_desc))
        if not finder:
            raise RuntimeError("Impossible de créer le finder NDI")

        try:
            time.sleep(2.0)

            no_sources = ctypes.c_uint32(0)
            sources_ptr = self.lib.NDIlib_find_get_current_sources(
                finder, ctypes.byref(no_sources)
            )
            count = int(no_sources.value or 0)
            if not sources_ptr or count <= 0:
                return []

            result: list[dict[str, str]] = []
            for i in range(count):
                src = sources_ptr[i]
                name = src.p_ndi_name.decode("utf-8") if src.p_ndi_name else "Unnamed"
                raw_url = src.u.p_url_address.decode("utf-8") if src.u.p_url_address else None
                raw_ip = src.u.p_ip_address.decode("utf-8") if src.u.p_ip_address else None

                url = raw_url or ""
                ip_from_url = _extract_ip_from_url(raw_url) if raw_url else None
                ip_display = ip_from_url or (raw_ip or "") or ""
                result.append(
                    {
                        "name": name,
                        "url": url,
                        "ip": ip_display,
                        "ip_raw": raw_ip or "",
                    }
                )
            return result
        finally:
            self.lib.NDIlib_find_destroy(finder)

    def create_receiver(self, source: dict[str, str], recv_name: str = "NDI Manager Preview"):
        source_name = source.get("name", "")
        recv_name_b = recv_name.encode("utf-8")

        finder = None
        try:
            finder_desc = NDIlib_find_create_t(
                show_local_sources=True,
                p_groups=None,
                p_extra_ips=None,
            )
            finder = self.lib.NDIlib_find_create_v2(ctypes.byref(finder_desc))
            if finder:
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
                            # Copier les champs pour éviter dépendance mémoire interne.
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
                                    self.lib.NDIlib_recv_connect(
                                        inst, ctypes.byref(src_owned)
                                    )
                                except Exception:
                                    pass

                                inst_key = int(inst)
                                self._ndi_keepalive[inst_key] = {
                                    "bytes": tuple(
                                        b
                                        for b in (recv_name_b, n_b, u_b, ip_b)
                                        if b is not None
                                        and isinstance(b, (bytes, bytearray))
                                        and len(b) > 0
                                    ),
                                    "source_struct": src_owned,
                                    "settings_struct": settings,
                                }
                                return inst
                            break
        except Exception:
            pass
        finally:
            if finder:
                try:
                    self.lib.NDIlib_find_destroy(finder)
                except Exception:
                    pass

        # Fallback heuristique basée sur url/ip.
        ndi_name_b = source.get("name", "").encode("utf-8")
        url_raw = source.get("url") or ""
        ip_raw = source.get("ip_raw") or ""
        ip_display = source.get("ip") or ""

        url_b = url_raw.encode("utf-8") if url_raw else b""
        ip_raw_b = ip_raw.encode("utf-8") if ip_raw else b""
        ip_display_b = ip_display.encode("utf-8") if ip_display else b""

        src = NDIlib_source_t()
        src.p_ndi_name = ndi_name_b if ndi_name_b else None

        if url_raw:
            src.u.p_url_address = url_b
            src.u.p_ip_address = None
        elif ip_raw:
            src.u.p_ip_address = ip_raw_b
            src.u.p_url_address = None
        elif ip_display:
            src.u.p_ip_address = ip_display_b
            src.u.p_url_address = None

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

        try:
            self.lib.NDIlib_recv_connect(inst, ctypes.byref(src))
        except Exception:
            pass

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
        if frame_type != 1:
            return None, frame_type
        return frame, frame_type

    def free_video_frame(self, recv_instance, frame: NDIlib_video_frame_v2_t):
        self.lib.NDIlib_recv_free_video_v2(recv_instance, ctypes.byref(frame))

    def destroy_receiver(self, recv_instance):
        if recv_instance:
            try:
                self._ndi_keepalive.pop(int(recv_instance), None)
            except Exception:
                pass
            self.lib.NDIlib_recv_destroy(recv_instance)

