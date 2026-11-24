from __future__ import annotations

import json
import re
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from PIL import Image, ImageTk


@dataclass(frozen=True)
class ExportTaskItem:
    """Snapshot imutável de um sprite para exportação em thread."""

    source_display: str
    rel_path: str
    original_name: str
    output_filename: str
    sprite_rgba: Image.Image
    anchor: tuple[float, float]


@dataclass
class SpriteEntry:
    """Representa um frame PNG e seu ponto de ancoragem em coordenadas originais."""

    path: Path
    source_dir: Path
    rel_path: str
    width: int
    height: int
    anchor: tuple[float, float]

    @property
    def uid(self) -> str:
        return self.path.resolve().as_posix()

    @property
    def source_dir_norm(self) -> str:
        return self.source_dir.resolve().as_posix()

    @property
    def list_label(self) -> str:
        return f"{self.path.name}  ({self.source_dir_norm})"


@dataclass
class ProjectConfig:
    """Configurações de canvas, ponto de destino, fontes e ancoragens."""

    canvas_width: int = 256
    canvas_height: int = 256
    target_x: float = 128.0
    target_y: float = 220.0
    input_dirs: list[str] = field(default_factory=list)
    reference_path: Optional[str] = None
    reference_rel_path: Optional[str] = None
    reference_name: Optional[str] = None
    anchors_by_path: dict[str, list[float]] = field(default_factory=dict)
    anchors_by_rel: dict[str, list[float]] = field(default_factory=dict)
    anchors_by_name: dict[str, list[float]] = field(default_factory=dict)


class SpriteAlignerEngine:
    """Lógica pura de carregamento, cálculo de ancoragem e composição alpha."""

    @staticmethod
    def load_rgba_sprite(path: Path) -> Image.Image:
        """Carrega uma imagem em modo RGBA sem modificar o arquivo original."""
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {path}")
        try:
            with Image.open(path) as img:
                img.load()
                return img.convert("RGBA")
        except Exception as exc:
            raise ValueError(f"Imagem inválida ou corrompida ({path.name}): {exc}") from exc

    @staticmethod
    def default_anchor_for_size(width: int, height: int) -> tuple[float, float]:
        """Ponto inicial padrão: centro horizontal e base inferior."""
        return (width / 2.0, float(height))

    @staticmethod
    def compute_paste_position(
        anchor: tuple[float, float],
        target: tuple[float, float],
    ) -> tuple[int, int]:
        """
        Calcula a posição superior-esquerda (x, y) no canvas final para que
        `anchor` (ax, ay) coincida com `target` (tx, ty):
            tx = paste_x + ax  =>  paste_x = round(tx - ax)
            ty = paste_y + ay  =>  paste_y = round(ty - ay)
        """
        paste_x = int(round(target[0] - anchor[0]))
        paste_y = int(round(target[1] - anchor[1]))
        return paste_x, paste_y

    @staticmethod
    def check_out_of_bounds(
        sprite_rgba: Image.Image,
        paste_x: int,
        paste_y: int,
        canvas_w: int,
        canvas_h: int,
    ) -> bool:
        """
        Retorna True apenas se a bounding box dos pixels não transparentes
        (canal Alpha > 0) ultrapassar os limites do canvas [0, canvas_w) x [0, canvas_h).
        Ignora margens transparentes e trata sprites totalmente transparentes com segurança.
        """
        rgba = sprite_rgba if sprite_rgba.mode == "RGBA" else sprite_rgba.convert("RGBA")
        bbox = rgba.getchannel("A").getbbox()
        if bbox is None:
            return False

        left, top, right, bottom = bbox
        vis_left = paste_x + left
        vis_top = paste_y + top
        vis_right = paste_x + right
        vis_bottom = paste_y + bottom

        return (
            vis_left < 0
            or vis_top < 0
            or vis_right > canvas_w
            or vis_bottom > canvas_h
        )

    @classmethod
    def compose_on_canvas(
        cls,
        sprite_rgba: Image.Image,
        anchor: tuple[float, float],
        canvas_w: int,
        canvas_h: int,
        target: tuple[float, float],
    ) -> tuple[Image.Image, tuple[int, int], bool]:
        """
        Compõe o sprite sobre um canvas RGBA transparente usando `Image.alpha_composite`.
        Não redimensiona nem recorta o sprite de origem.
        """
        if sprite_rgba.mode != "RGBA":
            sprite_rgba = sprite_rgba.convert("RGBA")

        paste_x, paste_y = cls.compute_paste_position(anchor, target)
        out_of_bounds = cls.check_out_of_bounds(sprite_rgba, paste_x, paste_y, canvas_w, canvas_h)

        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        overlay = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        overlay.alpha_composite(sprite_rgba, dest=(paste_x, paste_y))
        result = Image.alpha_composite(canvas, overlay)

        canvas.close()
        overlay.close()
        return result, (paste_x, paste_y), out_of_bounds

    @staticmethod
    def _sanitize_token(text: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
        return cleaned or "origem"

    @classmethod
    def _next_available_name(cls, base_name: str, used_names: set[str]) -> str:
        if base_name.lower() not in used_names:
            return base_name
        p = Path(base_name)
        stem = p.stem
        suffix = p.suffix or ".png"
        counter = 2
        while True:
            candidate = f"{stem}_{counter}{suffix}"
            if candidate.lower() not in used_names:
                return candidate
            counter += 1

    @classmethod
    def resolve_export_filenames(
        cls,
        entries: list[SpriteEntry],
        existing_output_names: Optional[set[str]] = None,
    ) -> dict[str, str]:
        """
        Gera nomes únicos na pasta de saída unificada sem sobrescrever arquivos
        já existentes no destino nem colidir entre sprites de pastas diferentes.
        """
        by_filename: dict[str, list[SpriteEntry]] = {}
        for entry in entries:
            by_filename.setdefault(entry.path.name, []).append(entry)

        resolved: dict[str, str] = {}
        used_names: set[str] = {
            name.lower() for name in (existing_output_names or set())
        }

        # Primeiro passa: nomes sem conflito entre sprites preservam o nome original
        # se não houver colisão com arquivos já existentes na pasta de saída.
        for filename, group in by_filename.items():
            if len(group) == 1:
                only = group[0]
                chosen = cls._next_available_name(filename, used_names)
                resolved[only.uid] = chosen
                used_names.add(chosen.lower())

        # Segunda passa: nomes conflitantes entre pastas recebem prefixo da pasta de origem
        # e sufixo incremental se ainda houver colisão.
        for filename, group in by_filename.items():
            if len(group) <= 1:
                continue
            stem = Path(filename).stem
            suffix = Path(filename).suffix or ".png"
            for entry in group:
                folder_token = cls._sanitize_token(entry.source_dir.name)
                prefixed = f"{folder_token}_{stem}{suffix}"
                chosen = cls._next_available_name(prefixed, used_names)
                resolved[entry.uid] = chosen
                used_names.add(chosen.lower())

        return resolved


class SpritePadronizador2App:
    """Interface gráfica Tkinter do Sprite Padronizador 2.0."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Sprite Padronizador 2.0 — Alinhamento por Ponto de Ancoragem")
        self.root.geometry("1180x780")
        self.root.minsize(980, 650)

        self.source_dirs: list[Path] = []
        self.entries: list[SpriteEntry] = []
        self.current_index: int = -1
        self.image_cache: dict[str, Image.Image] = {}
        self.saved_anchors_by_uid: dict[str, tuple[float, float]] = {}

        self.output_dir: Optional[Path] = None
        self.is_exporting: bool = False

        self.zoom: float = 1.0
        self.fit_to_screen: bool = True
        self.photo_image: Optional[ImageTk.PhotoImage] = None
        self._last_rendered_image: Optional[Image.Image] = None
        self._ref_label_to_uid: dict[str, str] = {}
        self._selected_ref_uid: Optional[str] = None

        self.output_path_var = tk.StringVar(value="Nenhuma pasta selecionada")
        self.view_mode_var = tk.StringVar(value="standardized")
        self.show_crosshair_var = tk.BooleanVar(value=True)

        self.canvas_w_var = tk.StringVar(value="256")
        self.canvas_h_var = tk.StringVar(value="256")
        self.target_x_var = tk.StringVar(value="128")
        self.target_y_var = tk.StringVar(value="220")

        self.ref_sprite_var = tk.StringVar(value="")
        self.sprite_info_var = tk.StringVar(value="Sprite: nenhum carregado")
        self.source_info_var = tk.StringVar(value="Origem: -")
        self.anchor_info_var = tk.StringVar(value="Âncora: -")
        self.bounds_info_var = tk.StringVar(value="Limites: -")
        self.status_var = tk.StringVar(value="Pronto. Adicione uma ou mais pastas com PNGs.")

        self._interactive_widgets: list[tk.Widget] = []
        self._build_ui()
        self._bind_events()

    def _register_interactive(self, widget: tk.Widget) -> tk.Widget:
        self._interactive_widgets.append(widget)
        return widget

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for w in self._interactive_widgets:
            try:
                if isinstance(w, ttk.Combobox):
                    w.configure(state="readonly" if enabled else tk.DISABLED)
                else:
                    w.configure(state=state)
            except tk.TclError:
                pass
        self.sprite_listbox.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        self.dirs_listbox.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _build_ui(self) -> None:
        root_pad = ttk.Frame(self.root, padding=10)
        root_pad.pack(fill=tk.BOTH, expand=True)
        root_pad.columnconfigure(1, weight=1)
        root_pad.rowconfigure(2, weight=1)

        top_bar = ttk.Frame(root_pad)
        top_bar.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=(0, 6))
        top_bar.columnconfigure(0, weight=1)

        dirs_panel = ttk.LabelFrame(top_bar, text="Pastas de Entrada (PNGs)", padding=6)
        dirs_panel.grid(row=0, column=0, sticky=tk.EW, padx=(0, 8))
        dirs_panel.columnconfigure(0, weight=1)

        dirs_list_frame = ttk.Frame(dirs_panel)
        dirs_list_frame.grid(row=0, column=0, rowspan=2, sticky=tk.EW, padx=(0, 8))
        dirs_list_frame.columnconfigure(0, weight=1)

        self.dirs_listbox = tk.Listbox(dirs_list_frame, height=3, exportselection=False)
        dirs_scroll = ttk.Scrollbar(dirs_list_frame, orient=tk.VERTICAL, command=self.dirs_listbox.yview)
        self.dirs_listbox.configure(yscrollcommand=dirs_scroll.set)
        self.dirs_listbox.grid(row=0, column=0, sticky=tk.EW)
        dirs_scroll.grid(row=0, column=1, sticky=tk.NS)

        self.btn_add_dir = self._register_interactive(
            ttk.Button(dirs_panel, text="Adicionar Pasta...", command=self._add_input_dir)
        )
        self.btn_add_dir.grid(row=0, column=1, sticky=tk.EW, pady=(0, 3))

        self.btn_remove_dir = self._register_interactive(
            ttk.Button(dirs_panel, text="Remover Pasta Selecionada", command=self._remove_selected_dir)
        )
        self.btn_remove_dir.grid(row=0, column=2, sticky=tk.EW, padx=(4, 0), pady=(0, 3))

        self.btn_clear_dirs = self._register_interactive(
            ttk.Button(dirs_panel, text="Limpar Lista", command=self._clear_input_dirs)
        )
        self.btn_clear_dirs.grid(row=1, column=1, columnspan=2, sticky=tk.EW)

        json_panel = ttk.LabelFrame(top_bar, text="Projeto JSON", padding=6)
        json_panel.grid(row=0, column=1, sticky=tk.NS)

        self.btn_save_json = self._register_interactive(
            ttk.Button(json_panel, text="Salvar JSON...", command=self._save_json)
        )
        self.btn_save_json.pack(fill=tk.X, pady=(0, 4))

        self.btn_load_json = self._register_interactive(
            ttk.Button(json_panel, text="Carregar JSON...", command=self._load_json)
        )
        self.btn_load_json.pack(fill=tk.X)

        out_bar = ttk.Frame(root_pad)
        out_bar.grid(row=1, column=0, columnspan=3, sticky=tk.EW, pady=(0, 8))
        out_bar.columnconfigure(1, weight=1)

        self.btn_out_dir = self._register_interactive(
            ttk.Button(out_bar, text="Pasta de Saída...", command=self._select_output_dir)
        )
        self.btn_out_dir.grid(row=0, column=0, padx=(0, 6))

        ttk.Entry(out_bar, textvariable=self.output_path_var, state="readonly").grid(
            row=0, column=1, sticky=tk.EW, padx=(0, 6)
        )

        self.export_btn = self._register_interactive(
            ttk.Button(out_bar, text="Exportar Todos os Sprites", command=self._start_export)
        )
        self.export_btn.grid(row=0, column=2)

        left_panel = ttk.LabelFrame(root_pad, text="Sprites Carregados", padding=6)
        left_panel.grid(row=2, column=0, sticky=tk.NS, padx=(0, 8))

        self.sprite_listbox = tk.Listbox(left_panel, width=38, exportselection=False)
        list_scroll = ttk.Scrollbar(left_panel, orient=tk.VERTICAL, command=self.sprite_listbox.yview)
        self.sprite_listbox.configure(yscrollcommand=list_scroll.set)
        self.sprite_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        nav_frame = ttk.Frame(root_pad)
        nav_frame.grid(row=3, column=0, sticky=tk.EW, padx=(0, 8), pady=(6, 0))
        nav_frame.columnconfigure(0, weight=1)
        nav_frame.columnconfigure(1, weight=1)

        self.btn_prev = self._register_interactive(
            ttk.Button(nav_frame, text="◀ Anterior", command=self._prev_sprite)
        )
        self.btn_prev.grid(row=0, column=0, sticky=tk.EW, padx=(0, 3))

        self.btn_next = self._register_interactive(
            ttk.Button(nav_frame, text="Próximo ▶", command=self._next_sprite)
        )
        self.btn_next.grid(row=0, column=1, sticky=tk.EW, padx=(3, 0))

        center_panel = ttk.LabelFrame(root_pad, text="Visualização e Ancoragem", padding=6)
        center_panel.grid(row=2, column=1, sticky=tk.NSEW)
        center_panel.columnconfigure(0, weight=1)
        center_panel.rowconfigure(1, weight=1)

        view_toolbar = ttk.Frame(center_panel)
        view_toolbar.grid(row=0, column=0, sticky=tk.EW, pady=(0, 6))

        for text, cmd in [
            ("Zoom +", self._zoom_in),
            ("Zoom -", self._zoom_out),
            ("Ajustar à Tela", self._set_fit_mode),
        ]:
            b = self._register_interactive(ttk.Button(view_toolbar, text=text, command=cmd))
            b.pack(side=tk.LEFT, padx=(0, 3))

        for label, mode in [("Padronizado", "standardized"), ("Original", "original")]:
            rb = self._register_interactive(
                ttk.Radiobutton(
                    view_toolbar,
                    text=label,
                    variable=self.view_mode_var,
                    value=mode,
                    command=self._render_current,
                )
            )
            rb.pack(side=tk.LEFT, padx=(6, 4))

        cb = self._register_interactive(
            ttk.Checkbutton(
                view_toolbar,
                text="Cruz / Linha de Referência",
                variable=self.show_crosshair_var,
                command=self._render_current,
            )
        )
        cb.pack(side=tk.LEFT, padx=(8, 0))

        self.canvas = tk.Canvas(
            center_panel, bg="#252526", highlightthickness=1, highlightbackground="#444"
        )
        self.canvas.grid(row=1, column=0, sticky=tk.NSEW)

        info_bar_1 = ttk.Frame(center_panel)
        info_bar_1.grid(row=2, column=0, sticky=tk.EW, pady=(6, 2))
        ttk.Label(info_bar_1, textvariable=self.sprite_info_var).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(info_bar_1, textvariable=self.anchor_info_var).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(info_bar_1, textvariable=self.bounds_info_var).pack(side=tk.LEFT)

        info_bar_2 = ttk.Frame(center_panel)
        info_bar_2.grid(row=3, column=0, sticky=tk.EW)
        ttk.Label(info_bar_2, textvariable=self.source_info_var).pack(side=tk.LEFT)

        right_panel = ttk.Frame(root_pad, width=300)
        right_panel.grid(row=2, column=2, rowspan=2, sticky=tk.NS, padx=(8, 0))

        canvas_box = ttk.LabelFrame(right_panel, text="Canvas Final e Destino", padding=8)
        canvas_box.pack(fill=tk.X, pady=(0, 8))
        canvas_box.columnconfigure(1, weight=1)
        canvas_box.columnconfigure(3, weight=1)

        ttk.Label(canvas_box, text="Largura:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self._register_interactive(
            ttk.Entry(canvas_box, textvariable=self.canvas_w_var, width=7)
        ).grid(row=0, column=1, sticky=tk.EW, padx=(4, 8), pady=2)

        ttk.Label(canvas_box, text="Altura:").grid(row=0, column=2, sticky=tk.W, pady=2)
        self._register_interactive(
            ttk.Entry(canvas_box, textvariable=self.canvas_h_var, width=7)
        ).grid(row=0, column=3, sticky=tk.EW, padx=(4, 0), pady=2)

        ttk.Label(canvas_box, text="Alvo X:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self._register_interactive(
            ttk.Entry(canvas_box, textvariable=self.target_x_var, width=7)
        ).grid(row=1, column=1, sticky=tk.EW, padx=(4, 8), pady=2)

        ttk.Label(canvas_box, text="Alvo Y:").grid(row=1, column=2, sticky=tk.W, pady=2)
        self._register_interactive(
            ttk.Entry(canvas_box, textvariable=self.target_y_var, width=7)
        ).grid(row=1, column=3, sticky=tk.EW, padx=(4, 0), pady=2)

        self._register_interactive(
            ttk.Button(canvas_box, text="Aplicar Canvas / Alvo", command=self._apply_canvas_settings)
        ).grid(row=2, column=0, columnspan=4, sticky=tk.EW, pady=(6, 0))

        anchor_box = ttk.LabelFrame(right_panel, text="Ponto de Ancoragem (Sprite Atual)", padding=8)
        anchor_box.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(
            anchor_box,
            text="Clique na imagem para definir o ponto anatômico do frame atual.",
            wraplength=265,
        ).pack(anchor=tk.W, pady=(0, 6))

        btn_row = ttk.Frame(anchor_box)
        btn_row.pack(fill=tk.X)
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        self._register_interactive(
            ttk.Button(btn_row, text="Base / Centro", command=self._reset_anchor_default)
        ).grid(row=0, column=0, sticky=tk.EW, padx=(0, 3))
        self._register_interactive(
            ttk.Button(btn_row, text="Centro Geométrico", command=self._reset_anchor_center)
        ).grid(row=0, column=1, sticky=tk.EW, padx=(3, 0))

        ref_box = ttk.LabelFrame(right_panel, text="Referência Compartilhada", padding=8)
        ref_box.pack(fill=tk.X, pady=(0, 8))
        ref_box.columnconfigure(0, weight=1)

        self.ref_combo = self._register_interactive(
            ttk.Combobox(ref_box, textvariable=self.ref_sprite_var, state="readonly")
        )
        self.ref_combo.grid(row=0, column=0, sticky=tk.EW, pady=(0, 6))

        for label, cmd in [
            ("Definir Sprite Atual como Referência", self._set_current_as_ref),
            ("Copiar Âncora da Referência p/ Atual", self._copy_ref_to_current),
            ("Aplicar Âncora da Referência a Todos", self._apply_ref_to_all),
        ]:
            self._register_interactive(ttk.Button(ref_box, text=label, command=cmd)).grid(
                sticky=tk.EW, pady=(0, 4)
            )

        log_box = ttk.LabelFrame(right_panel, text="Registro de Operações", padding=6)
        log_box.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_box, height=8, width=34, state=tk.DISABLED, wrap=tk.WORD)
        log_scroll = ttk.Scrollbar(log_box, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        bottom_bar = ttk.Frame(root_pad)
        bottom_bar.grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=(8, 0))
        bottom_bar.columnconfigure(1, weight=1)

        self.progress = ttk.Progressbar(bottom_bar, mode="determinate", length=240)
        self.progress.grid(row=0, column=0, padx=(0, 10))
        ttk.Label(bottom_bar, textvariable=self.status_var).grid(row=0, column=1, sticky=tk.W)

    def _bind_events(self) -> None:
        self.sprite_listbox.bind("<<ListboxSelect>>", self._on_listbox_select)
        self.ref_combo.bind("<<ComboboxSelected>>", self._on_ref_combo_select)
        self.canvas.bind("<Configure>", lambda _e: self._render_current())
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.root.bind("<Left>", lambda _e: self._prev_sprite() if not self.is_exporting else None)
        self.root.bind("<Right>", lambda _e: self._next_sprite() if not self.is_exporting else None)

    def _log(self, msg: str) -> None:
        def append() -> None:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        self.root.after(0, append)

    def _get_sprite_image(self, entry: SpriteEntry) -> Image.Image:
        key = entry.uid
        if key not in self.image_cache:
            self.image_cache[key] = SpriteAlignerEngine.load_rgba_sprite(entry.path)
        return self.image_cache[key]

    def _parse_canvas_config(self) -> Optional[tuple[int, int, float, float]]:
        try:
            cw = int(self.canvas_w_var.get().strip())
            ch = int(self.canvas_h_var.get().strip())
            tx = float(self.target_x_var.get().strip().replace(",", "."))
            ty = float(self.target_y_var.get().strip().replace(",", "."))
            if cw <= 0 or ch <= 0:
                raise ValueError("Largura e altura do canvas devem ser maiores que zero.")
            return cw, ch, tx, ty
        except ValueError as exc:
            messagebox.showerror("Configuração inválida", f"Verifique canvas e alvo:\n{exc}")
            return None

    def _apply_canvas_settings(self) -> None:
        if self.is_exporting:
            return
        parsed = self._parse_canvas_config()
        if parsed is None:
            return
        cw, ch, tx, ty = parsed
        self._log(f"Canvas configurado: {cw}x{ch} px | Alvo: ({tx:.1f}, {ty:.1f}).")
        self._render_current()

    def _snapshot_current_anchors(self) -> None:
        for entry in self.entries:
            self.saved_anchors_by_uid[entry.uid] = entry.anchor

    def _rebuild_sprites_from_dirs(self, preferred_uid: Optional[str] = None) -> None:
        """Reconstrói a lista de sprites a partir de `self.source_dirs` preservando âncoras."""
        self._snapshot_current_anchors()
        previous_uid = preferred_uid
        if previous_uid is None and self._current_entry() is not None:
            previous_uid = self._current_entry().uid  # type: ignore[union-attr]

        new_entries: list[SpriteEntry] = []
        seen_file_uids: set[str] = set()
        loaded_count = 0

        for src_dir in self.source_dirs:
            resolved_src = src_dir.resolve()
            if not resolved_src.exists() or not resolved_src.is_dir():
                self._log(f"[AVISO] Pasta indisponível ignorada: {resolved_src.as_posix()}")
                continue

            pngs = sorted(
                [p for p in resolved_src.iterdir() if p.is_file() and p.suffix.lower() == ".png"]
            )
            for p in pngs:
                resolved_p = p.resolve()
                file_uid = resolved_p.as_posix()
                if file_uid in seen_file_uids:
                    continue
                seen_file_uids.add(file_uid)

                try:
                    rgba = self.image_cache.get(file_uid)
                    if rgba is None:
                        rgba = SpriteAlignerEngine.load_rgba_sprite(resolved_p)
                        self.image_cache[file_uid] = rgba
                    w, h = rgba.size
                    anchor = self.saved_anchors_by_uid.get(
                        file_uid, SpriteAlignerEngine.default_anchor_for_size(w, h)
                    )
                    rel_str = resolved_p.relative_to(resolved_src).as_posix()
                    new_entries.append(
                        SpriteEntry(
                            path=resolved_p,
                            source_dir=resolved_src,
                            rel_path=rel_str,
                            width=w,
                            height=h,
                            anchor=anchor,
                        )
                    )
                    loaded_count += 1
                except Exception as exc:
                    self._log(f"[AVISO] Ignorado {p.name}: {exc}")

        self.entries = new_entries
        self.sprite_listbox.delete(0, tk.END)
        for entry in self.entries:
            self.sprite_listbox.insert(tk.END, entry.list_label)

        self._refresh_reference_combo()

        if not self.entries:
            self.current_index = -1
            self.status_var.set("Nenhum sprite carregado. Adicione pastas com PNGs.")
            self._render_current()
            return

        target_idx = 0
        if previous_uid:
            for idx, entry in enumerate(self.entries):
                if entry.uid == previous_uid:
                    target_idx = idx
                    break

        self.status_var.set(
            f"{loaded_count} sprite(s) em {len(self.source_dirs)} pasta(s)."
        )
        self._select_index(target_idx)

    def _refresh_dirs_listbox(self) -> None:
        self.dirs_listbox.delete(0, tk.END)
        for d in self.source_dirs:
            self.dirs_listbox.insert(tk.END, d.resolve().as_posix())

    def _refresh_reference_combo(self) -> None:
        self._ref_label_to_uid.clear()
        labels: list[str] = []
        for entry in self.entries:
            label = f"{entry.path.name} ({entry.source_dir_norm})"
            if label in self._ref_label_to_uid:
                label = f"{entry.uid}"
            self._ref_label_to_uid[label] = entry.uid
            labels.append(label)

        self.ref_combo["values"] = labels
        if not labels:
            self._selected_ref_uid = None
            self.ref_sprite_var.set("")
            return

        if self._selected_ref_uid:
            for lbl, uid in self._ref_label_to_uid.items():
                if uid == self._selected_ref_uid:
                    self.ref_sprite_var.set(lbl)
                    return

        first_label = labels[0]
        self._selected_ref_uid = self._ref_label_to_uid[first_label]
        self.ref_sprite_var.set(first_label)

    def _on_ref_combo_select(self, _event: tk.Event) -> None:
        lbl = self.ref_sprite_var.get()
        if lbl in self._ref_label_to_uid:
            self._selected_ref_uid = self._ref_label_to_uid[lbl]

    def _add_input_dir(self) -> None:
        if self.is_exporting:
            return
        folder = filedialog.askdirectory(title="Adicionar pasta com sprites PNG")
        if not folder:
            return

        resolved_dir = Path(folder).resolve()
        if any(existing.resolve() == resolved_dir for existing in self.source_dirs):
            messagebox.showinfo("Pasta já adicionada", "Essa pasta já faz parte da lista de fontes.")
            return

        pngs = sorted(
            [p for p in resolved_dir.iterdir() if p.is_file() and p.suffix.lower() == ".png"]
        )
        if not pngs:
            messagebox.showwarning("Sem PNGs", "Nenhum arquivo .png foi encontrado na pasta selecionada.")
            return

        self.source_dirs.append(resolved_dir)
        self._refresh_dirs_listbox()
        self._log(f"Pasta adicionada: {resolved_dir.as_posix()} ({len(pngs)} PNG(s)).")
        self._rebuild_sprites_from_dirs()

    def _remove_selected_dir(self) -> None:
        if self.is_exporting:
            return
        sel = self.dirs_listbox.curselection()
        if not sel:
            messagebox.showinfo("Seleção necessária", "Selecione uma pasta na lista para remover.")
            return

        idx = sel[0]
        removed = self.source_dirs.pop(idx)
        self._refresh_dirs_listbox()
        self._log(f"Pasta removida: {removed.resolve().as_posix()}")
        self._rebuild_sprites_from_dirs()

    def _clear_input_dirs(self) -> None:
        if self.is_exporting or not self.source_dirs:
            return
        self._snapshot_current_anchors()
        self.source_dirs.clear()
        self._refresh_dirs_listbox()
        self._rebuild_sprites_from_dirs()
        self._log("Lista de pastas de entrada limpa.")

    def _select_output_dir(self) -> None:
        if self.is_exporting:
            return
        folder = filedialog.askdirectory(title="Selecione a pasta de saída para os PNGs")
        if folder:
            self.output_dir = Path(folder).resolve()
            self.output_path_var.set(self.output_dir.as_posix())
            self._log(f"Pasta de saída definida: {self.output_dir.as_posix()}")

    def _select_index(self, idx: int) -> None:
        if not self.entries:
            return
        idx = max(0, min(idx, len(self.entries) - 1))
        self.current_index = idx
        self.sprite_listbox.selection_clear(0, tk.END)
        self.sprite_listbox.selection_set(idx)
        self.sprite_listbox.see(idx)
        self._render_current()

    def _on_listbox_select(self, _event: tk.Event) -> None:
        if self.is_exporting:
            return
        sel = self.sprite_listbox.curselection()
        if sel:
            self._select_index(sel[0])

    def _prev_sprite(self) -> None:
        if self.current_index > 0:
            self._select_index(self.current_index - 1)

    def _next_sprite(self) -> None:
        if self.current_index < len(self.entries) - 1:
            self._select_index(self.current_index + 1)

    def _zoom_in(self) -> None:
        self.fit_to_screen = False
        self.zoom = min(8.0, self.zoom * 1.25)
        self._render_current()

    def _zoom_out(self) -> None:
        self.fit_to_screen = False
        self.zoom = max(0.1, self.zoom / 1.25)
        self._render_current()

    def _set_fit_mode(self) -> None:
        self.fit_to_screen = True
        self._render_current()

    def _current_entry(self) -> Optional[SpriteEntry]:
        if 0 <= self.current_index < len(self.entries):
            return self.entries[self.current_index]
        return None

    def _reset_anchor_default(self) -> None:
        if self.is_exporting:
            return
        entry = self._current_entry()
        if not entry:
            return
        entry.anchor = SpriteAlignerEngine.default_anchor_for_size(entry.width, entry.height)
        self.saved_anchors_by_uid[entry.uid] = entry.anchor
        self._log(f"{entry.list_label}: âncora redefinida para base/centro {entry.anchor}.")
        self._render_current()

    def _reset_anchor_center(self) -> None:
        if self.is_exporting:
            return
        entry = self._current_entry()
        if not entry:
            return
        entry.anchor = (entry.width / 2.0, entry.height / 2.0)
        self.saved_anchors_by_uid[entry.uid] = entry.anchor
        self._log(f"{entry.list_label}: âncora redefinida para centro {entry.anchor}.")
        self._render_current()

    def _get_reference_entry(self) -> Optional[SpriteEntry]:
        if self._selected_ref_uid:
            for e in self.entries:
                if e.uid == self._selected_ref_uid:
                    return e
        lbl = self.ref_sprite_var.get()
        uid = self._ref_label_to_uid.get(lbl)
        if uid:
            for e in self.entries:
                if e.uid == uid:
                    return e
        return self.entries[0] if self.entries else None

    def _set_current_as_ref(self) -> None:
        if self.is_exporting:
            return
        entry = self._current_entry()
        if not entry:
            return
        self._selected_ref_uid = entry.uid
        self._refresh_reference_combo()
        self._log(
            f"Referência definida para '{entry.list_label}' ({entry.anchor[0]:.1f}, {entry.anchor[1]:.1f})."
        )

    def _copy_ref_to_current(self) -> None:
        if self.is_exporting:
            return
        entry = self._current_entry()
        ref = self._get_reference_entry()
        if not entry or not ref:
            return
        entry.anchor = (ref.anchor[0], ref.anchor[1])
        self.saved_anchors_by_uid[entry.uid] = entry.anchor
        self._log(f"{entry.list_label}: copiada âncora de '{ref.list_label}' -> {entry.anchor}.")
        self._render_current()

    def _apply_ref_to_all(self) -> None:
        if self.is_exporting:
            return
        ref = self._get_reference_entry()
        if not ref or not self.entries:
            return
        for e in self.entries:
            e.anchor = (ref.anchor[0], ref.anchor[1])
            self.saved_anchors_by_uid[e.uid] = e.anchor
        self._log(
            f"Âncora de '{ref.list_label}' {ref.anchor} aplicada a todos os {len(self.entries)} sprites."
        )
        self._render_current()

    def _on_canvas_click(self, event: tk.Event) -> None:
        if self.is_exporting:
            return
        entry = self._current_entry()
        if not entry or self._last_rendered_image is None:
            return

        cw_widget = max(1, self.canvas.winfo_width())
        ch_widget = max(1, self.canvas.winfo_height())
        img_w, img_h = self._last_rendered_image.size

        disp_w = max(1, int(round(img_w * self.zoom)))
        disp_h = max(1, int(round(img_h * self.zoom)))
        offset_x = (cw_widget - disp_w) / 2.0
        offset_y = (ch_widget - disp_h) / 2.0

        rel_disp_x = (event.x - offset_x) / self.zoom
        rel_disp_y = (event.y - offset_y) / self.zoom

        if not (0.0 <= rel_disp_x <= img_w and 0.0 <= rel_disp_y <= img_h):
            return

        if self.view_mode_var.get() == "original":
            ax = max(0.0, min(float(entry.width), rel_disp_x))
            ay = max(0.0, min(float(entry.height), rel_disp_y))
        else:
            parsed = self._parse_canvas_config()
            if parsed is None:
                return
            _, _, tx, ty = parsed
            paste_x, paste_y = SpriteAlignerEngine.compute_paste_position(entry.anchor, (tx, ty))
            ax = max(0.0, min(float(entry.width), rel_disp_x - paste_x))
            ay = max(0.0, min(float(entry.height), rel_disp_y - paste_y))

        entry.anchor = (round(ax, 1), round(ay, 1))
        self.saved_anchors_by_uid[entry.uid] = entry.anchor
        self._log(f"{entry.list_label}: nova âncora em ({entry.anchor[0]:.1f}, {entry.anchor[1]:.1f}).")
        self._render_current()

    def _render_current(self) -> None:
        self.canvas.delete("all")
        entry = self._current_entry()
        if not entry:
            self.sprite_info_var.set("Sprite: nenhum carregado")
            self.source_info_var.set("Origem: -")
            self.anchor_info_var.set("Âncora: -")
            self.bounds_info_var.set("Limites: -")
            self._last_rendered_image = None
            return

        parsed = self._parse_canvas_config()
        if parsed is None:
            return
        canvas_w, canvas_h, tx, ty = parsed

        sprite_rgba = self._get_sprite_image(entry)
        composed, (paste_x, paste_y), out_of_bounds = SpriteAlignerEngine.compose_on_canvas(
            sprite_rgba, entry.anchor, canvas_w, canvas_h, (tx, ty)
        )

        if self.view_mode_var.get() == "original":
            composed.close()
            source_img = sprite_rgba
            anchor_in_view = entry.anchor
        else:
            source_img = composed
            anchor_in_view = (paste_x + entry.anchor[0], paste_y + entry.anchor[1])

        cw_widget = max(1, self.canvas.winfo_width())
        ch_widget = max(1, self.canvas.winfo_height())

        if self.fit_to_screen:
            scale_x = (cw_widget - 24) / max(1, source_img.width)
            scale_y = (ch_widget - 24) / max(1, source_img.height)
            self.zoom = max(0.1, min(8.0, min(scale_x, scale_y)))

        disp_w = max(1, int(round(source_img.width * self.zoom)))
        disp_h = max(1, int(round(source_img.height * self.zoom)))
        offset_x = (cw_widget - disp_w) / 2.0
        offset_y = (ch_widget - disp_h) / 2.0

        checker_size = max(6, int(round(8 * self.zoom)))
        for gy in range(0, disp_h, checker_size):
            for gx in range(0, disp_w, checker_size):
                color = "#3a3a3d" if ((gx // checker_size + gy // checker_size) % 2 == 0) else "#2d2d30"
                x0 = offset_x + gx
                y0 = offset_y + gy
                x1 = min(offset_x + disp_w, x0 + checker_size)
                y1 = min(offset_y + disp_h, y0 + checker_size)
                self.canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

        resized = source_img.resize((disp_w, disp_h), Image.Resampling.NEAREST)
        self._last_rendered_image = source_img
        self.photo_image = ImageTk.PhotoImage(resized)
        self.canvas.create_image(offset_x, offset_y, anchor=tk.NW, image=self.photo_image)

        self.canvas.create_rectangle(
            offset_x, offset_y, offset_x + disp_w, offset_y + disp_h, outline="#777777", width=1
        )

        if self.show_crosshair_var.get():
            ax_disp = offset_x + anchor_in_view[0] * self.zoom
            ay_disp = offset_y + anchor_in_view[1] * self.zoom

            self.canvas.create_line(
                offset_x, ay_disp, offset_x + disp_w, ay_disp, fill="#00e5ff", dash=(4, 3), width=1
            )
            self.canvas.create_line(
                ax_disp, offset_y, ax_disp, offset_y + disp_h, fill="#00e5ff", dash=(4, 3), width=1
            )
            r = 6
            self.canvas.create_oval(
                ax_disp - r, ay_disp - r, ax_disp + r, ay_disp + r, outline="#ff3d00", width=2
            )

        self.sprite_info_var.set(
            f"Sprite: {entry.path.name} ({entry.width}x{entry.height}px) | Zoom: {self.zoom:.2f}x"
        )
        self.source_info_var.set(f"Origem: {entry.uid}")
        self.anchor_info_var.set(f"Âncora: ({entry.anchor[0]:.1f}, {entry.anchor[1]:.1f})")
        self.bounds_info_var.set(
            "Limites: ULTRAPASSA O CANVAS!" if out_of_bounds else "Limites: OK"
        )

    def _save_json(self) -> None:
        if self.is_exporting:
            return
        parsed = self._parse_canvas_config()
        if parsed is None:
            return
        cw, ch, tx, ty = parsed

        file_path = filedialog.asksaveasfilename(
            title="Salvar configuração de pontos de ancoragem",
            defaultextension=".json",
            filetypes=[("Arquivos JSON", "*.json")],
        )
        if not file_path:
            return

        self._snapshot_current_anchors()
        ref = self._get_reference_entry()

        anchors_by_path: dict[str, list[float]] = {}
        anchors_by_rel: dict[str, list[float]] = {}
        anchors_by_name: dict[str, list[float]] = {}
        sprite_paths: list[str] = []

        # Preserva também âncoras conhecidas no cache caso alguma pasta esteja temporariamente ausente
        for uid, anc in self.saved_anchors_by_uid.items():
            norm_uid = Path(uid).resolve().as_posix()
            anchors_by_path[norm_uid] = [float(anc[0]), float(anc[1])]

        for entry in self.entries:
            norm_path = entry.uid
            sprite_paths.append(norm_path)
            anchors_by_path[norm_path] = [float(entry.anchor[0]), float(entry.anchor[1])]
            folder_key = entry.source_dir.name or entry.source_dir_norm
            rel_key = f"{folder_key}/{entry.rel_path}"
            anchors_by_rel[rel_key] = [float(entry.anchor[0]), float(entry.anchor[1])]
            anchors_by_name[entry.path.name] = [float(entry.anchor[0]), float(entry.anchor[1])]

        ref_path_str: Optional[str] = ref.uid if ref is not None else None
        ref_rel: Optional[str] = None
        if ref is not None:
            ref_folder = ref.source_dir.name or ref.source_dir_norm
            ref_rel = f"{ref_folder}/{ref.rel_path}"

        cfg = ProjectConfig(
            canvas_width=cw,
            canvas_height=ch,
            target_x=tx,
            target_y=ty,
            input_dirs=[d.resolve().as_posix() for d in self.source_dirs],
            reference_path=ref_path_str,
            reference_rel_path=ref_rel,
            reference_name=ref.path.name if ref else None,
            anchors_by_path=anchors_by_path,
            anchors_by_rel=anchors_by_rel,
            anchors_by_name=anchors_by_name,
        )

        payload = {
            "version": 3,
            "canvas_width": cfg.canvas_width,
            "canvas_height": cfg.canvas_height,
            "target_x": cfg.target_x,
            "target_y": cfg.target_y,
            "input_dirs": cfg.input_dirs,
            "sprites": sprite_paths,
            "reference_path": cfg.reference_path,
            "reference_rel_path": cfg.reference_rel_path,
            "reference_name": cfg.reference_name,
            "anchors_by_path": cfg.anchors_by_path,
            "anchors_by_rel": cfg.anchors_by_rel,
            "anchors": cfg.anchors_by_name,
        }

        try:
            Path(file_path).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self._log(f"Projeto JSON salvo em '{Path(file_path).resolve().as_posix()}'.")
        except Exception as exc:
            messagebox.showerror("Erro ao salvar JSON", str(exc))

    @staticmethod
    def _parse_anchor_pair(val: object) -> Optional[tuple[float, float]]:
        if isinstance(val, (list, tuple)) and len(val) == 2:
            try:
                return (float(val[0]), float(val[1]))
            except (TypeError, ValueError):
                return None
        return None

    def _load_json(self) -> None:
        if self.is_exporting:
            return
        file_path = filedialog.askopenfilename(
            title="Carregar configuração de pontos de ancoragem",
            filetypes=[("Arquivos JSON", "*.json")],
        )
        if not file_path:
            return

        try:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Estrutura JSON inválida.")

            cw = int(data["canvas_width"])
            ch = int(data["canvas_height"])
            tx = float(data["target_x"])
            ty = float(data["target_y"])
            if cw <= 0 or ch <= 0:
                raise ValueError("Dimensões do canvas no JSON devem ser positivas.")

            self.canvas_w_var.set(str(cw))
            self.canvas_h_var.set(str(ch))
            self.target_x_var.set(str(tx))
            self.target_y_var.set(str(ty))

            raw_dirs = data.get("input_dirs", [])
            if not isinstance(raw_dirs, list):
                raw_dirs = []

            valid_dirs: list[Path] = []
            missing_dirs: list[str] = []
            seen_dirs: set[str] = set()

            for d_str in raw_dirs:
                p = Path(str(d_str))
                res = p.resolve().as_posix()
                if res in seen_dirs:
                    continue
                seen_dirs.add(res)
                if p.exists() and p.is_dir():
                    valid_dirs.append(p.resolve())
                else:
                    missing_dirs.append(p.resolve().as_posix())

            anchors_by_path_raw: dict = data.get("anchors_by_path", {}) or {}
            anchors_by_rel_raw: dict = data.get("anchors_by_rel", {}) or {}
            anchors_by_name_raw: dict = data.get("anchors", {}) or {}

            # Normaliza chaves de caminho completo
            normalized_by_path: dict[str, tuple[float, float]] = {}
            if isinstance(anchors_by_path_raw, dict):
                for k, v in anchors_by_path_raw.items():
                    pair = self._parse_anchor_pair(v)
                    if pair is not None:
                        norm_k = Path(str(k)).resolve().as_posix()
                        normalized_by_path[norm_k] = pair
                        self.saved_anchors_by_uid[norm_k] = pair

            # Preserva no cache âncoras legadas por caminho relativo / nome sem descartá-las
            legacy_by_rel: dict[str, tuple[float, float]] = {}
            if isinstance(anchors_by_rel_raw, dict):
                for k, v in anchors_by_rel_raw.items():
                    pair = self._parse_anchor_pair(v)
                    if pair is not None:
                        legacy_by_rel[str(k)] = pair

            legacy_by_name: dict[str, tuple[float, float]] = {}
            if isinstance(anchors_by_name_raw, dict):
                for k, v in anchors_by_name_raw.items():
                    pair = self._parse_anchor_pair(v)
                    if pair is not None:
                        legacy_by_name[str(k)] = pair

            self.source_dirs = valid_dirs
            self._refresh_dirs_listbox()
            self._rebuild_sprites_from_dirs()

            applied_exact = 0
            applied_legacy = 0
            for entry in self.entries:
                folder_key = entry.source_dir.name or entry.source_dir_norm
                rel_key = f"{folder_key}/{entry.rel_path}"

                if entry.uid in normalized_by_path:
                    entry.anchor = normalized_by_path[entry.uid]
                    self.saved_anchors_by_uid[entry.uid] = entry.anchor
                    applied_exact += 1
                elif rel_key in legacy_by_rel:
                    entry.anchor = legacy_by_rel[rel_key]
                    self.saved_anchors_by_uid[entry.uid] = entry.anchor
                    applied_legacy += 1
                elif entry.path.name in legacy_by_name:
                    entry.anchor = legacy_by_name[entry.path.name]
                    self.saved_anchors_by_uid[entry.uid] = entry.anchor
                    applied_legacy += 1

            ref_path_raw = data.get("reference_path")
            ref_rel = data.get("reference_rel_path")
            ref_name = data.get("reference_name")
            matched_ref: Optional[SpriteEntry] = None

            if isinstance(ref_path_raw, str) and ref_path_raw.strip():
                norm_ref_path = Path(ref_path_raw).resolve().as_posix()
                for entry in self.entries:
                    if entry.uid == norm_ref_path:
                        matched_ref = entry
                        break

            if matched_ref is None and isinstance(ref_rel, str) and ref_rel.strip():
                for entry in self.entries:
                    folder_key = entry.source_dir.name or entry.source_dir_norm
                    if f"{folder_key}/{entry.rel_path}" == ref_rel:
                        matched_ref = entry
                        break

            if matched_ref is None and isinstance(ref_name, str) and ref_name.strip():
                for entry in self.entries:
                    if entry.path.name == ref_name:
                        matched_ref = entry
                        break

            if matched_ref is not None:
                self._selected_ref_uid = matched_ref.uid
                self._refresh_reference_combo()

            self._render_current()
            self._log(
                f"JSON carregado: {len(valid_dirs)} pasta(s) ativa(s), "
                f"{applied_exact} âncora(s) por caminho, {applied_legacy} migrada(s) de formato anterior."
            )

            if missing_dirs:
                msg_missing = "\n".join(f"• {d}" for d in missing_dirs)
                self._log(f"[AVISO] Pastas ausentes no JSON: {', '.join(missing_dirs)}")
                messagebox.showwarning(
                    "Algumas pastas não foram encontradas",
                    "As seguintes pastas do projeto não existem mais e foram ignoradas, "
                    "mas suas âncoras salvas foram preservadas e você pode continuar com "
                    "as fontes disponíveis:\n\n"
                    f"{msg_missing}",
                )

        except Exception as exc:
            messagebox.showerror("Erro ao carregar JSON", f"JSON inválido ou incompatível:\n{exc}")

    def _start_export(self) -> None:
        if self.is_exporting:
            return
        if not self.entries:
            messagebox.showwarning(
                "Sem sprites", "Adicione ao menos uma pasta com sprites PNG antes de exportar."
            )
            return
        if not self.output_dir:
            messagebox.showwarning("Pasta de saída", "Selecione a pasta de saída antes de exportar.")
            return

        out_resolved = self.output_dir.resolve()
        if any(src.resolve() == out_resolved for src in self.source_dirs):
            messagebox.showerror(
                "Pasta inválida",
                "A pasta de saída não pode ser igual a nenhuma pasta de entrada.",
            )
            return

        parsed = self._parse_canvas_config()
        if parsed is None:
            return
        cw, ch, tx, ty = parsed

        out_of_bounds_items: list[str] = []
        for entry in self.entries:
            sprite_rgba = self._get_sprite_image(entry)
            px, py = SpriteAlignerEngine.compute_paste_position(entry.anchor, (tx, ty))
            if SpriteAlignerEngine.check_out_of_bounds(sprite_rgba, px, py, cw, ch):
                out_of_bounds_items.append(entry.list_label)

        if out_of_bounds_items:
            preview_list = "\n".join(f"• {item}" for item in out_of_bounds_items[:8])
            extra = "\n..." if len(out_of_bounds_items) > 8 else ""
            proceed = messagebox.askyesno(
                "Aviso: Sprite fora dos limites do canvas",
                f"{len(out_of_bounds_items)} sprite(s) ultrapassam os limites do canvas ({cw}x{ch}):\n"
                f"{preview_list}{extra}\n\n"
                "Deseja exportar mesmo assim?",
            )
            if not proceed:
                return

        existing_names: set[str] = set()
        if out_resolved.exists() and out_resolved.is_dir():
            existing_names = {
                p.name for p in out_resolved.iterdir() if p.is_file()
            }

        filename_map = SpriteAlignerEngine.resolve_export_filenames(
            self.entries, existing_output_names=existing_names
        )
        snapshot_tasks: list[ExportTaskItem] = []
        renamed_log: list[str] = []

        for entry in self.entries:
            out_name = filename_map[entry.uid]
            if out_name != entry.path.name:
                renamed_log.append(f"{entry.list_label} -> {out_name}")
            rgba_copy = self._get_sprite_image(entry).copy()
            snapshot_tasks.append(
                ExportTaskItem(
                    source_display=entry.list_label,
                    rel_path=entry.rel_path,
                    original_name=entry.path.name,
                    output_filename=out_name,
                    sprite_rgba=rgba_copy,
                    anchor=(entry.anchor[0], entry.anchor[1]),
                )
            )

        for item_desc in renamed_log:
            self._log(f"[NOME AJUSTADO] {item_desc}")

        self.is_exporting = True
        self._set_controls_enabled(False)
        self.progress.configure(maximum=len(snapshot_tasks), value=0)
        self.status_var.set("Exportando sprites...")

        threading.Thread(
            target=self._export_worker,
            args=(out_resolved, cw, ch, tx, ty, snapshot_tasks),
            daemon=True,
        ).start()

    def _export_worker(
        self,
        out_dir: Path,
        cw: int,
        ch: int,
        tx: float,
        ty: float,
        tasks: list[ExportTaskItem],
    ) -> None:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            total = len(tasks)

            for idx, item in enumerate(tasks, start=1):
                try:
                    composed, _, _ = SpriteAlignerEngine.compose_on_canvas(
                        item.sprite_rgba, item.anchor, cw, ch, (tx, ty)
                    )
                    dest = out_dir / item.output_filename
                    composed.save(dest, format="PNG")
                    composed.close()
                finally:
                    item.sprite_rgba.close()

                self.root.after(
                    0,
                    lambda i=idx, name=item.output_filename: (
                        self.progress.configure(value=i),
                        self.status_var.set(f"Exportando {i}/{total}: {name}"),
                    ),
                )

            self._log(f"Exportação concluída: {total} PNG(s) salvos em '{out_dir.as_posix()}'.")
            self.root.after(
                0,
                lambda: (
                    self.status_var.set(f"Concluído! {total} sprites exportados ({cw}x{ch} px)."),
                    messagebox.showinfo(
                        "Exportação concluída",
                        f"{total} sprites salvos em:\n{out_dir.as_posix()}",
                    ),
                ),
            )
        except Exception as exc:
            error_msg = str(exc) or exc.__class__.__name__
            for rem in tasks:
                try:
                    rem.sprite_rgba.close()
                except Exception:
                    pass
            self._log(f"[ERRO na exportação] {error_msg}")
            self.root.after(
                0,
                lambda msg=error_msg: (
                    self.status_var.set("Erro na exportação."),
                    messagebox.showerror("Erro", msg),
                ),
            )
        finally:
            def finish_ui() -> None:
                self.is_exporting = False
                self._set_controls_enabled(True)

            self.root.after(0, finish_ui)


if __name__ == "__main__":
    root = tk.Tk()
    app = SpritePadronizador2App(root)
    root.mainloop()