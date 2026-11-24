import math
import os
import re
import shutil
from collections import deque
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk


def natural_sort_key(path: str) -> list[object]:
    """Chave de ordenação natural (ex.: frame_1, frame_2, frame_10)."""
    base = os.path.basename(path).lower()
    parts = re.split(r"(\d+)", base)
    return [int(part) if part.isdigit() else part for part in parts]


def make_checkerboard(width: int, height: int, tile: int = 8) -> Image.Image:
    """Gera fundo xadrez cinza para evidenciar canais alfa/transparência."""
    w = max(1, width)
    h = max(1, height)
    checker = Image.new("RGBA", (w, h), (200, 200, 200, 255))
    c_arr = np.array(checker)
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            if ((x // tile) + (y // tile)) % 2 == 1:
                c_arr[y : y + tile, x : x + tile, :3] = 140
    return Image.fromarray(c_arr, mode="RGBA")


class AnimationAssemblerWindow:
    """Janela de montagem, reordenação, pré-visualização e exportação de animações."""

    SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")

    def __init__(self, parent: tk.Tk, parse_hex_fn) -> None:
        self.parent = parent
        self._parse_hex_color = parse_hex_fn

        self.win = tk.Toplevel(parent)
        self.win.title("Montador e Pré-Visualizador de Animação (Godot)")
        self.win.geometry("1120x680")
        self.win.minsize(920, 560)

        # Lista de dicts: {"path": str, "name": str, "image": Image.Image}
        self.frames: list[dict[str, object]] = []
        self.current_idx: int = 0
        self.is_playing: bool = False
        self._after_id: str | None = None
        self._preview_photo: ImageTk.PhotoImage | None = None

        self._build_ui()
        self._update_bg_controls()
        self._refresh_frame_list(preserve_selection=False)
        self._render_current_frame()
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------------------------------------------------------------
    # Interface em 3 Áreas
    # -------------------------------------------------------------------------
    def _build_ui(self) -> None:
        root_pane = ttk.PanedWindow(self.win, orient=tk.HORIZONTAL)
        root_pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Área 1: Lista de frames importados
        left_panel = ttk.LabelFrame(root_pane, text="1. Frames da Animação", padding=8)
        root_pane.add(left_panel, weight=1)

        imp_row1 = ttk.Frame(left_panel)
        imp_row1.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(imp_row1, text="Importar Imagens...", command=self.import_files).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4)
        )
        ttk.Button(imp_row1, text="Importar Pasta...", command=self.import_folder).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )

        ord_row = ttk.Frame(left_panel)
        ord_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(ord_row, text="Subir ▲", command=self.move_frame_up).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(ord_row, text="Descer ▼", command=self.move_frame_down).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4)
        )
        ttk.Button(ord_row, text="Remover", command=self.remove_selected_frame).pack(side=tk.LEFT)

        list_wrap = ttk.Frame(left_panel)
        list_wrap.pack(fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL)
        self.frame_listbox = tk.Listbox(list_wrap, yscrollcommand=sb.set, exportselection=False)
        sb.config(command=self.frame_listbox.yview)
        self.frame_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.frame_listbox.bind("<<ListboxSelect>>", self._on_select_frame)

        ttk.Button(left_panel, text="Limpar Lista", command=self.clear_frames).pack(fill=tk.X, pady=(6, 0))

        # Área 2: Preview da animação
        center_panel = ttk.LabelFrame(root_pane, text="2. Pré-Visualização da Animação", padding=8)
        root_pane.add(center_panel, weight=3)

        self.preview_canvas = tk.Canvas(center_panel, bg="#181c24", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", lambda _e: self._render_current_frame())

        status_bar = ttk.Frame(center_panel)
        status_bar.pack(fill=tk.X, pady=(6, 0))
        self.frame_counter_var = tk.StringVar(value="Frame: 0 / 0")
        ttk.Label(status_bar, textvariable=self.frame_counter_var, font=("TkDefaultFont", 10, "bold")).pack(
            side=tk.LEFT
        )
        self.frame_meta_var = tk.StringVar(value="Nenhum frame carregado.")
        ttk.Label(status_bar, textvariable=self.frame_meta_var).pack(side=tk.RIGHT)

        # Área 3: Configurações, reprodução e exportação
        right_panel = ttk.Frame(root_pane, padding=4)
        root_pane.add(right_panel, weight=1)

        pb_box = ttk.LabelFrame(right_panel, text="3. Reprodução", padding=8)
        pb_box.pack(fill=tk.X, pady=(0, 8))

        transport_row = ttk.Frame(pb_box)
        transport_row.pack(fill=tk.X, pady=(0, 6))
        self.play_btn_var = tk.StringVar(value="▶ Play")
        ttk.Button(transport_row, textvariable=self.play_btn_var, command=self.toggle_play).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4)
        )
        ttk.Button(transport_row, text="⏹ Reiniciar", command=self.restart_animation).pack(side=tk.LEFT)

        step_row = ttk.Frame(pb_box)
        step_row.pack(fill=tk.X)
        ttk.Button(step_row, text="◀ Anterior", command=self.prev_frame).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4)
        )
        ttk.Button(step_row, text="Próximo ▶", command=self.next_frame).pack(side=tk.LEFT, fill=tk.X, expand=True)

        cfg_box = ttk.LabelFrame(right_panel, text="Configurações da Animação", padding=8)
        cfg_box.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(cfg_box, text="FPS:").pack(anchor=tk.W)
        self.fps_var = tk.IntVar(value=12)
        ttk.Spinbox(cfg_box, from_=1, to=120, textvariable=self.fps_var, width=8).pack(anchor=tk.W, pady=(0, 4))

        ttk.Label(cfg_box, text="Velocidade (0.1x - 4.0x):").pack(anchor=tk.W)
        self.speed_var = tk.DoubleVar(value=1.0)
        speed_row = ttk.Frame(cfg_box)
        speed_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Scale(speed_row, from_=0.1, to=4.0, variable=self.speed_var, orient=tk.HORIZONTAL).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self.speed_lbl_var = tk.StringVar(value="1.0x")
        ttk.Label(speed_row, textvariable=self.speed_lbl_var, width=5).pack(side=tk.RIGHT)
        self.speed_var.trace_add("write", lambda *_a: self.speed_lbl_var.set(f"{max(0.1, float(self.speed_var.get())):.1f}x"))

        ttk.Label(cfg_box, text="Escala de Visualização:").pack(anchor=tk.W)
        self.scale_var = tk.DoubleVar(value=2.0)
        scale_row = ttk.Frame(cfg_box)
        scale_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Scale(
            scale_row,
            from_=0.5,
            to=8.0,
            variable=self.scale_var,
            orient=tk.HORIZONTAL,
            command=lambda _v: self._render_current_frame(),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.scale_lbl_var = tk.StringVar(value="2.0x")
        ttk.Label(scale_row, textvariable=self.scale_lbl_var, width=5).pack(side=tk.RIGHT)
        self.scale_var.trace_add("write", lambda *_a: self.scale_lbl_var.set(f"{float(self.scale_var.get()):.1f}x"))

        self.loop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg_box, text="Reproduzir em loop", variable=self.loop_var).pack(anchor=tk.W)

        ttk.Label(cfg_box, text="Fundo do Preview:").pack(anchor=tk.W, pady=(6, 2))
        self.preview_bg_mode_var = tk.StringVar(value="checker")
        bg_modes = ttk.Frame(cfg_box)
        bg_modes.pack(fill=tk.X)
        ttk.Radiobutton(
            bg_modes, text="Xadrez", value="checker", variable=self.preview_bg_mode_var, command=self._update_bg_controls
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Radiobutton(
            bg_modes, text="Sólido", value="solid", variable=self.preview_bg_mode_var, command=self._update_bg_controls
        ).pack(side=tk.LEFT)

        self.solid_bg_row = ttk.Frame(cfg_box)
        self.solid_bg_row.pack(fill=tk.X, pady=(4, 0))
        self.preview_bg_hex_var = tk.StringVar(value="#2b303a")
        self.bg_hex_entry = ttk.Entry(self.solid_bg_row, textvariable=self.preview_bg_hex_var, width=9)
        self.bg_hex_entry.pack(side=tk.LEFT, padx=(0, 4))
        self.bg_swatch = tk.Label(
            self.solid_bg_row,
            background=self.preview_bg_hex_var.get(),
            width=3,
            relief=tk.SOLID,
            borderwidth=1,
        )
        self.bg_swatch.pack(side=tk.LEFT, padx=(0, 4))
        self.bg_pick_btn = ttk.Button(self.solid_bg_row, text="Cor...", command=self._pick_preview_bg, width=6)
        self.bg_pick_btn.pack(side=tk.LEFT)
        self.preview_bg_hex_var.trace_add("write", lambda *_a: self._on_bg_hex_change())

        exp_box = ttk.LabelFrame(right_panel, text="Exportação para Godot", padding=8)
        exp_box.pack(fill=tk.X)

        ttk.Label(exp_box, text="Prefixo:").pack(anchor=tk.W)
        self.anim_prefix_var = tk.StringVar(value="anim_frame")
        ttk.Entry(exp_box, textvariable=self.anim_prefix_var).pack(fill=tk.X, pady=(0, 6))

        ttk.Button(exp_box, text="Exportar Sequência Ordenada...", command=self.export_sequence).pack(
            fill=tk.X, pady=(0, 4)
        )
        ttk.Button(exp_box, text="Exportar Spritesheet Horizontal...", command=lambda: self.export_sheet("horizontal")).pack(
            fill=tk.X, pady=(0, 4)
        )
        ttk.Button(exp_box, text="Exportar Spritesheet Vertical...", command=lambda: self.export_sheet("vertical")).pack(
            fill=tk.X
        )

    def _update_bg_controls(self) -> None:
        is_solid = self.preview_bg_mode_var.get() == "solid"
        state = tk.NORMAL if is_solid else tk.DISABLED
        self.bg_hex_entry.configure(state=state)
        self.bg_pick_btn.configure(state=state)
        self._render_current_frame()

    def _on_bg_hex_change(self) -> None:
        rgb = self._parse_hex_color(self.preview_bg_hex_var.get())
        if rgb is not None:
            self.bg_swatch.configure(background=f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}")
        self._render_current_frame()

    def _pick_preview_bg(self) -> None:
        initial = self.preview_bg_hex_var.get()
        if self._parse_hex_color(initial) is None:
            initial = "#2b303a"
        chosen = colorchooser.askcolor(color=initial, title="Escolher Cor de Fundo do Preview", parent=self.win)
        if chosen and chosen[1]:
            self.preview_bg_hex_var.set(chosen[1].lower())

    # -------------------------------------------------------------------------
    # Importação e Reordenação (Sem Alterar Arquivos Originais)
    # -------------------------------------------------------------------------
    def _load_frame_items(self, paths: list[str]) -> tuple[int, list[str]]:
        loaded = 0
        errors: list[str] = []
        for path in sorted(paths, key=natural_sort_key):
            try:
                with Image.open(path) as raw:
                    rgba = raw.convert("RGBA").copy()
                self.frames.append({"path": path, "name": os.path.basename(path), "image": rgba})
                loaded += 1
            except Exception as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
        return loaded, errors

    def import_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecionar Frames da Animação",
            parent=self.win,
            filetypes=[
                ("Imagens de frames", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not paths:
            return
        loaded, errors = self._load_frame_items(list(paths))
        if loaded > 0 and self.current_idx >= len(self.frames):
            self.current_idx = 0
        self._refresh_frame_list(preserve_selection=True)
        self._render_current_frame()
        if errors:
            messagebox.showwarning("Arquivos ignorados", "Alguns arquivos não puderam ser carregados:\n" + "\n".join(errors[:8]), parent=self.win)

    def import_folder(self) -> None:
        folder = filedialog.askdirectory(title="Selecionar Pasta com Frames", parent=self.win)
        if not folder:
            return
        try:
            entries = [
                os.path.join(folder, fname)
                for fname in os.listdir(folder)
                if os.path.isfile(os.path.join(folder, fname)) and fname.lower().endswith(self.SUPPORTED_EXTS)
            ]
        except Exception as exc:
            messagebox.showerror("Erro ao ler pasta", f"Não foi possível acessar a pasta:\n{exc}", parent=self.win)
            return

        if not entries:
            messagebox.showinfo("Pasta vazia", "Nenhuma imagem compatível encontrada na pasta selecionada.", parent=self.win)
            return

        loaded, errors = self._load_frame_items(entries)
        if loaded > 0 and self.current_idx >= len(self.frames):
            self.current_idx = 0
        self._refresh_frame_list(preserve_selection=True)
        self._render_current_frame()
        if errors:
            messagebox.showwarning("Arquivos ignorados", "Alguns arquivos não puderam ser carregados:\n" + "\n".join(errors[:8]), parent=self.win)

    def _refresh_frame_list(self, preserve_selection: bool = True) -> None:
        self.frame_listbox.delete(0, tk.END)
        for idx, item in enumerate(self.frames, start=1):
            w, h = item["image"].size
            self.frame_listbox.insert(tk.END, f"{idx:03d} | {item['name']} ({w}x{h})")

        if not self.frames:
            self.current_idx = 0
            self.frame_counter_var.set("Frame: 0 / 0")
            self.frame_meta_var.set("Nenhum frame carregado.")
            return

        if preserve_selection:
            self.current_idx = max(0, min(self.current_idx, len(self.frames) - 1))
        self.frame_listbox.selection_clear(0, tk.END)
        self.frame_listbox.selection_set(self.current_idx)
        self.frame_listbox.see(self.current_idx)
        self._update_frame_labels()

    def _update_frame_labels(self) -> None:
        total = len(self.frames)
        if total == 0:
            self.frame_counter_var.set("Frame: 0 / 0")
            self.frame_meta_var.set("Nenhum frame carregado.")
            return
        idx = max(0, min(self.current_idx, total - 1))
        item = self.frames[idx]
        w, h = item["image"].size
        self.frame_counter_var.set(f"Frame: {idx + 1} / {total}")
        self.frame_meta_var.set(f"{item['name']} • {w}x{h}px")

    def _on_select_frame(self, _event: tk.Event) -> None:
        sel = self.frame_listbox.curselection()
        if sel:
            self.current_idx = int(sel[0])
            self._update_frame_labels()
            self._render_current_frame()

    def move_frame_up(self) -> None:
        if len(self.frames) < 2 or self.current_idx <= 0:
            return
        i = self.current_idx
        self.frames[i - 1], self.frames[i] = self.frames[i], self.frames[i - 1]
        self.current_idx = i - 1
        self._refresh_frame_list(preserve_selection=True)
        self._render_current_frame()

    def move_frame_down(self) -> None:
        if len(self.frames) < 2 or self.current_idx >= len(self.frames) - 1:
            return
        i = self.current_idx
        self.frames[i + 1], self.frames[i] = self.frames[i], self.frames[i + 1]
        self.current_idx = i + 1
        self._refresh_frame_list(preserve_selection=True)
        self._render_current_frame()

    def remove_selected_frame(self) -> None:
        if not self.frames:
            return
        del self.frames[self.current_idx]
        if self.current_idx >= len(self.frames):
            self.current_idx = max(0, len(self.frames) - 1)
        self._refresh_frame_list(preserve_selection=True)
        self._render_current_frame()

    def clear_frames(self) -> None:
        self._stop_playback()
        self.frames = []
        self.current_idx = 0
        self._refresh_frame_list(preserve_selection=False)
        self._render_current_frame()

    # -------------------------------------------------------------------------
    # Controle de Reprodução e Renderização
    # -------------------------------------------------------------------------
    def toggle_play(self) -> None:
        if not self.frames:
            messagebox.showwarning("Aviso", "Importe ao menos um frame para reproduzir.", parent=self.win)
            return
        if self.is_playing:
            self._stop_playback()
        else:
            if not self.loop_var.get() and self.current_idx >= len(self.frames) - 1:
                self.current_idx = 0
            self.is_playing = True
            self.play_btn_var.set("⏸ Pause")
            self._schedule_next_tick()

    def _stop_playback(self) -> None:
        self.is_playing = False
        self.play_btn_var.set("▶ Play")
        if self._after_id is not None:
            try:
                self.win.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def restart_animation(self) -> None:
        if not self.frames:
            return
        self.current_idx = 0
        self._refresh_frame_list(preserve_selection=True)
        self._render_current_frame()

    def prev_frame(self) -> None:
        if not self.frames:
            return
        self._stop_playback()
        self.current_idx = (self.current_idx - 1) % len(self.frames)
        self._refresh_frame_list(preserve_selection=True)
        self._render_current_frame()

    def next_frame(self) -> None:
        if not self.frames:
            return
        self._stop_playback()
        self.current_idx = (self.current_idx + 1) % len(self.frames)
        self._refresh_frame_list(preserve_selection=True)
        self._render_current_frame()

    def _schedule_next_tick(self) -> None:
        if not self.is_playing or not self.frames:
            return
        fps = max(1, int(self.fps_var.get()))
        speed = max(0.1, float(self.speed_var.get()))
        delay_ms = max(10, int(round(1000.0 / (fps * speed))))
        self._after_id = self.win.after(delay_ms, self._advance_frame)

    def _advance_frame(self) -> None:
        self._after_id = None
        if not self.is_playing or not self.frames:
            return

        if self.current_idx + 1 < len(self.frames):
            self.current_idx += 1
        elif self.loop_var.get():
            self.current_idx = 0
        else:
            self._stop_playback()
            self._refresh_frame_list(preserve_selection=True)
            self._render_current_frame()
            return

        self._refresh_frame_list(preserve_selection=True)
        self._render_current_frame()
        self._schedule_next_tick()

    def _render_current_frame(self) -> None:
        self.preview_canvas.delete("all")
        if not self.frames:
            return

        idx = max(0, min(self.current_idx, len(self.frames) - 1))
        src_rgba: Image.Image = self.frames[idx]["image"]
        scale = max(0.25, min(12.0, float(self.scale_var.get())))
        disp_w = max(1, int(round(src_rgba.width * scale)))
        disp_h = max(1, int(round(src_rgba.height * scale)))

        resample = Image.Resampling.NEAREST if scale >= 1.5 else Image.Resampling.BILINEAR
        scaled_rgba = src_rgba.resize((disp_w, disp_h), resample)

        if self.preview_bg_mode_var.get() == "solid":
            rgb = self._parse_hex_color(self.preview_bg_hex_var.get()) or (43, 48, 58)
            bg_img = Image.new("RGBA", (disp_w, disp_h), (rgb[0], rgb[1], rgb[2], 255))
        else:
            bg_img = make_checkerboard(disp_w, disp_h, tile=8)

        composed = Image.alpha_composite(bg_img, scaled_rgba)
        self._preview_photo = ImageTk.PhotoImage(composed)

        cw = max(1, self.preview_canvas.winfo_width())
        ch = max(1, self.preview_canvas.winfo_height())
        self.preview_canvas.create_image(cw // 2, ch // 2, anchor=tk.CENTER, image=self._preview_photo)

    # -------------------------------------------------------------------------
    # Exportação de Sequência e Spritesheet
    # -------------------------------------------------------------------------
    def export_sequence(self) -> None:
        if not self.frames:
            messagebox.showwarning("Aviso", "Não há frames para exportar.", parent=self.win)
            return
        out_dir = filedialog.askdirectory(title="Escolher Pasta para Sequência de Frames", parent=self.win)
        if not out_dir:
            return
        prefix = self.anim_prefix_var.get().strip() or "frame"
        try:
            for i, item in enumerate(self.frames, start=1):
                dest_path = os.path.join(out_dir, f"{prefix}_{i:03d}.png")
                src_path = item["path"]
                if os.path.exists(src_path) and src_path.lower().endswith(".png"):
                    shutil.copy2(src_path, dest_path)
                else:
                    item["image"].save(dest_path, format="PNG")
        except Exception as exc:
            messagebox.showerror("Erro na exportação", f"Falha ao exportar sequência:\n{exc}", parent=self.win)
            return
        messagebox.showinfo("Exportado", f"{len(self.frames)} frames salvos em:\n{out_dir}", parent=self.win)

    def export_sheet(self, orientation: str) -> None:
        if not self.frames:
            messagebox.showwarning("Aviso", "Não há frames para montar a spritesheet.", parent=self.win)
            return
        out_path = filedialog.asksaveasfilename(
            title="Salvar Spritesheet PNG",
            parent=self.win,
            defaultextension=".png",
            initialfile=f"{self.anim_prefix_var.get().strip() or 'anim'}_{orientation}.png",
            filetypes=[("PNG", "*.png")],
        )
        if not out_path:
            return

        imgs: list[Image.Image] = [item["image"] for item in self.frames]
        if orientation == "horizontal":
            total_w = sum(im.width for im in imgs)
            total_h = max(im.height for im in imgs)
            sheet = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
            x_cursor = 0
            for im in imgs:
                sheet.paste(im, (x_cursor, 0), im)
                x_cursor += im.width
        else:
            total_w = max(im.width for im in imgs)
            total_h = sum(im.height for im in imgs)
            sheet = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
            y_cursor = 0
            for im in imgs:
                sheet.paste(im, (0, y_cursor), im)
                y_cursor += im.height

        try:
            sheet.save(out_path, format="PNG")
        except Exception as exc:
            messagebox.showerror("Erro ao salvar", f"Falha ao gerar spritesheet:\n{exc}", parent=self.win)
            return
        messagebox.showinfo("Spritesheet salva", f"Spritesheet salva em:\n{out_path}", parent=self.win)

    def _on_close(self) -> None:
        self._stop_playback()
        self.win.destroy()


class SpriteExtractorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Extrator Automático de Spritesheets (2D)")
        self.root.geometry("1360x840")
        self.root.minsize(1024, 640)

        # Estado da imagem e dos retângulos [x1, y1, x2, y2] em coordenadas originais
        self.image_path: str | None = None
        self.pil_image: Image.Image | None = None
        self.cv_bgr: np.ndarray | None = None

        self.rects: list[list[int]] = []
        self.selected_idx: int | None = None
        self.copied_rect: list[int] | None = None
        self._paste_offset_step: int = 0
        self._anim_window: AnimationAssemblerWindow | None = None

        # Estado de visualização (zoom e pan)
        self.zoom: float = 1.0
        self.min_zoom: float = 0.15
        self.max_zoom: float = 12.0
        self.pan_x: float = 0.0
        self.pan_y: float = 0.0

        # Interação do mouse no canvas
        self.drag_mode: str | None = None  # "pan", "create", "move", "resize", "eyedropper"
        self.resize_edge: str | None = None  # "nw", "ne", "sw", "se", "n", "s", "w", "e"
        self.drag_start_canvas: tuple[int, int] = (0, 0)
        self.drag_start_img: tuple[float, float] = (0.0, 0.0)
        self.original_rect_on_drag: list[int] | None = None
        self._eyedropper_active: bool = False

        self._photo_cache: ImageTk.PhotoImage | None = None

        self._build_ui()
        self._bind_events()

    # -------------------------------------------------------------------------
    # Interface Gráfica
    # -------------------------------------------------------------------------
    def _build_ui(self) -> None:
        top_bar = ttk.Frame(self.root, padding=6)
        top_bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top_bar, text="Abrir Spritesheet", command=self.open_image).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(top_bar, text="Detectar Sprites", command=self.detect_sprites).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(top_bar, text="Ordenar Leitura", command=self.sort_and_refresh).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(top_bar, text="Resetar Zoom", command=self.reset_view).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(top_bar, text="Montar Animação...", command=self.open_animation_assembler).pack(
            side=tk.LEFT, padx=(0, 12)
        )

        self.count_var = tk.StringVar(value="Sprites detectados: 0")
        ttk.Label(top_bar, textvariable=self.count_var, font=("TkDefaultFont", 10, "bold")).pack(side=tk.LEFT, padx=8)

        self.info_var = tk.StringVar(value="Abra um arquivo de imagem para começar.")
        ttk.Label(top_bar, textvariable=self.info_var).pack(side=tk.RIGHT, padx=8)

        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        canvas_frame = ttk.Frame(main_pane)
        main_pane.add(canvas_frame, weight=4)

        self.canvas = tk.Canvas(canvas_frame, bg="#181c24", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        side_panel = ttk.Frame(main_pane, width=355, padding=6)
        main_pane.add(side_panel, weight=1)

        det_box = ttk.LabelFrame(side_panel, text="1. Parâmetros de Detecção", padding=8)
        det_box.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(det_box, text="Sensibilidade de Cor (Azul-Claro):").pack(anchor=tk.W)
        self.color_tol_var = tk.IntVar(value=32)
        ttk.Scale(det_box, from_=8, to=90, variable=self.color_tol_var, orient=tk.HORIZONTAL).pack(fill=tk.X)

        ttk.Label(det_box, text="Área Mínima (px²):").pack(anchor=tk.W, pady=(4, 0))
        self.min_area_var = tk.IntVar(value=120)
        ttk.Spinbox(det_box, from_=10, to=50000, increment=20, textvariable=self.min_area_var, width=10).pack(anchor=tk.W)

        ttk.Label(det_box, text="Margem de Expansão (px):").pack(anchor=tk.W, pady=(4, 0))
        self.pad_var = tk.IntVar(value=0)
        ttk.Spinbox(det_box, from_=-10, to=20, increment=1, textvariable=self.pad_var, width=10).pack(anchor=tk.W)

        self.split_adj_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            det_box,
            text="Tentar dividir blocos largos adjacentes",
            variable=self.split_adj_var,
        ).pack(anchor=tk.W, pady=(6, 0))

        edit_box = ttk.LabelFrame(side_panel, text="2. Retângulos & Correção Manual", padding=8)
        edit_box.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        mode_row = ttk.Frame(edit_box)
        mode_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(mode_row, text="Modo do Mouse:").pack(side=tk.LEFT)
        self.tool_mode_var = tk.StringVar(value="select_edit")
        ttk.Radiobutton(mode_row, text="Selecionar/Ajustar", value="select_edit", variable=self.tool_mode_var).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Radiobutton(mode_row, text="Criar Novo", value="create", variable=self.tool_mode_var).pack(
            side=tk.LEFT, padx=4
        )

        btn_row = ttk.Frame(edit_box)
        btn_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(btn_row, text="Copiar (Ctrl+C)", command=self.copy_selected_rect).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="Colar (Ctrl+V)", command=self.paste_copied_rect).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="Remover", command=self.delete_selected).pack(side=tk.LEFT)

        grid_row = ttk.Frame(edit_box)
        grid_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(grid_row, text="Criar Grade de Frames...", command=self.open_frame_grid_dialog).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4)
        )
        ttk.Button(grid_row, text="Limpar Tudo", command=self.clear_rects).pack(side=tk.LEFT)

        list_frame = ttk.Frame(edit_box)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.rect_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, exportselection=False)
        scrollbar.config(command=self.rect_listbox.yview)
        self.rect_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        exp_box = ttk.LabelFrame(side_panel, text="3. Remoção de Fundo & Exportação", padding=8)
        exp_box.pack(fill=tk.X)

        ttk.Label(exp_box, text="Modo de Remoção:").pack(anchor=tk.W)
        self.bg_mode_var = tk.StringVar(value="flood_fill")
        bg_mode_row = ttk.Frame(exp_box)
        bg_mode_row.pack(fill=tk.X, pady=(2, 4))
        ttk.Radiobutton(
            bg_mode_row, text="Flood Fill", value="flood_fill", variable=self.bg_mode_var, command=self._update_bg_ui_state
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Radiobutton(
            bg_mode_row, text="Chroma Key", value="chroma_key", variable=self.bg_mode_var, command=self._update_bg_ui_state
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Radiobutton(
            bg_mode_row, text="Nenhum", value="none", variable=self.bg_mode_var, command=self._update_bg_ui_state
        ).pack(side=tk.LEFT)

        self.chroma_frame = ttk.Frame(exp_box)
        self.chroma_frame.pack(fill=tk.X, pady=(2, 4))

        ttk.Label(self.chroma_frame, text="Cor Alvo (RGB/Hex):").pack(side=tk.LEFT)
        self.chroma_color_var = tk.StringVar(value="#8eb8e5")
        self.chroma_entry = ttk.Entry(self.chroma_frame, textvariable=self.chroma_color_var, width=9)
        self.chroma_entry.pack(side=tk.LEFT, padx=(4, 4))

        self.color_swatch = tk.Label(
            self.chroma_frame,
            background=self.chroma_color_var.get(),
            width=3,
            relief=tk.SOLID,
            borderwidth=1,
        )
        self.color_swatch.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(self.chroma_frame, text="Cor...", command=self.pick_chroma_color, width=6).pack(
            side=tk.LEFT, padx=(0, 2)
        )
        ttk.Button(self.chroma_frame, text="Conta-Gotas", command=self.activate_eyedropper, width=10).pack(
            side=tk.LEFT
        )

        self.chroma_color_var.trace_add("write", lambda *_args: self._sync_swatch_color())

        ttk.Label(exp_box, text="Tolerância de Cor (0-150):").pack(anchor=tk.W, pady=(4, 0))
        self.bg_tol_var = tk.IntVar(value=35)
        self.bg_tol_scale = ttk.Scale(exp_box, from_=0, to=150, variable=self.bg_tol_var, orient=tk.HORIZONTAL)
        self.bg_tol_scale.pack(fill=tk.X)

        prefix_row = ttk.Frame(exp_box)
        prefix_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(prefix_row, text="Prefixo:").pack(side=tk.LEFT)
        self.prefix_var = tk.StringVar(value="reimu")
        ttk.Entry(prefix_row, textvariable=self.prefix_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        action_exp_row = ttk.Frame(exp_box)
        action_exp_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(action_exp_row, text="Prévia do Sprite", command=self.preview_selected_sprite).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(action_exp_row, text="Exportar PNGs...", command=self.export_sprites).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )

        self._update_bg_ui_state()

    def open_animation_assembler(self) -> None:
        if self._anim_window is not None and self._anim_window.win.winfo_exists():
            self._anim_window.win.lift()
            self._anim_window.win.focus_force()
            return
        self._anim_window = AnimationAssemblerWindow(self.root, self._parse_hex_color)

    def _sync_swatch_color(self) -> None:
        rgb = self._parse_hex_color(self.chroma_color_var.get())
        if rgb is not None:
            self.color_swatch.configure(background=f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}")

    @staticmethod
    def _parse_hex_color(value: str) -> tuple[int, int, int] | None:
        s = value.strip()
        if s.startswith("#"):
            s = s[1:]
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6:
            return None
        try:
            r = int(s[0:2], 16)
            g = int(s[2:4], 16)
            b = int(s[4:6], 16)
            return (r, g, b)
        except ValueError:
            return None

    def _update_bg_ui_state(self) -> None:
        mode = self.bg_mode_var.get()
        is_chroma = mode == "chroma_key"
        for child in self.chroma_frame.winfo_children():
            child.configure(state=tk.NORMAL if is_chroma else tk.DISABLED)
        self.bg_tol_scale.configure(state=tk.NORMAL if mode != "none" else tk.DISABLED)

    def pick_chroma_color(self) -> None:
        initial = self.chroma_color_var.get()
        if self._parse_hex_color(initial) is None:
            initial = "#8eb8e5"
        chosen = colorchooser.askcolor(color=initial, title="Escolher Cor do Chroma Key", parent=self.root)
        if chosen and chosen[1]:
            self.chroma_color_var.set(chosen[1].lower())

    def activate_eyedropper(self) -> None:
        if self.pil_image is None:
            messagebox.showwarning("Aviso", "Abra uma imagem primeiro.")
            return
        self._eyedropper_active = True
        self.canvas.configure(cursor="crosshair")
        self.info_var.set("Conta-gotas ativo: clique na imagem para capturar a cor do Chroma Key.")

    def _bind_events(self) -> None:
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)

        self.canvas.bind("<ButtonPress-2>", self.on_pan_start)
        self.canvas.bind("<B2-Motion>", self.on_pan_drag)
        self.canvas.bind("<ButtonPress-3>", self.on_pan_start)
        self.canvas.bind("<B3-Motion>", self.on_pan_drag)

        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.zoom_at(1.15, e.x, e.y))
        self.canvas.bind("<Button-5>", lambda e: self.zoom_at(1 / 1.15, e.x, e.y))

        self.rect_listbox.bind("<<ListboxSelect>>", self.on_listbox_select)
        self.root.bind("<Delete>", lambda _e: self.delete_selected())

        self.root.bind("<Control-c>", self._on_ctrl_c)
        self.root.bind("<Control-C>", self._on_ctrl_c)
        self.root.bind("<Control-v>", self._on_ctrl_v)
        self.root.bind("<Control-V>", self._on_ctrl_v)

    def _on_ctrl_c(self, _event: tk.Event) -> str:
        self.copy_selected_rect()
        return "break"

    def _on_ctrl_v(self, _event: tk.Event) -> str:
        self.paste_copied_rect()
        return "break"

    # -------------------------------------------------------------------------
    # Coordenadas (Imagem <-> Canvas)
    # -------------------------------------------------------------------------
    def img_to_canvas(self, ix: float, iy: float) -> tuple[float, float]:
        return ix * self.zoom + self.pan_x, iy * self.zoom + self.pan_y

    def canvas_to_img(self, cx: float, cy: float) -> tuple[float, float]:
        return (cx - self.pan_x) / self.zoom, (cy - self.pan_y) / self.zoom

    def reset_view(self) -> None:
        if self.pil_image is None:
            return
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        iw, ih = self.pil_image.size
        scale_w = (cw - 40) / iw
        scale_h = (ch - 40) / ih
        self.zoom = max(self.min_zoom, min(min(scale_w, scale_h), 2.0))
        self.pan_x = (cw - iw * self.zoom) / 2.0
        self.pan_y = (ch - ih * self.zoom) / 2.0
        self.redraw()

    def zoom_at(self, factor: float, cx: float, cy: float) -> None:
        if self.pil_image is None:
            return
        ix, iy = self.canvas_to_img(cx, cy)
        new_zoom = max(self.min_zoom, min(self.max_zoom, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 1e-6:
            return
        self.zoom = new_zoom
        self.pan_x = cx - ix * self.zoom
        self.pan_y = cy - iy * self.zoom
        self.redraw()

    def on_mouse_wheel(self, event: tk.Event) -> None:
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        self.zoom_at(factor, event.x, event.y)

    def on_pan_start(self, event: tk.Event) -> None:
        self.drag_mode = "pan"
        self.drag_start_canvas = (event.x, event.y)

    def on_pan_drag(self, event: tk.Event) -> None:
        if self.drag_mode != "pan":
            return
        dx = event.x - self.drag_start_canvas[0]
        dy = event.y - self.drag_start_canvas[1]
        self.pan_x += dx
        self.pan_y += dy
        self.drag_start_canvas = (event.x, event.y)
        self.redraw()

    # -------------------------------------------------------------------------
    # Abertura e Detecção Automática
    # -------------------------------------------------------------------------
    def open_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecionar Spritesheet",
            filetypes=[
                ("Imagens suportadas", "*.png *.jpg *.jpeg *.bmp *.webp"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not path:
            return

        try:
            pil_img = Image.open(path).convert("RGB")
        except Exception as exc:
            messagebox.showerror("Erro ao abrir", f"Não foi possível carregar a imagem:\n{exc}")
            return

        self.image_path = path
        self.pil_image = pil_img
        self.cv_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        self.rects = []
        self.selected_idx = None
        self.copied_rect = None
        self._paste_offset_step = 0

        base_name = os.path.splitext(os.path.basename(path))[0].split("-")[0].lower()
        if base_name:
            self.prefix_var.set(base_name)

        self.reset_view()
        self.info_var.set(f"{os.path.basename(path)} ({pil_img.width}x{pil_img.height}px)")
        self.detect_sprites()

    def _estimate_light_blue_mask(self, tolerance: int) -> np.ndarray:
        assert self.cv_bgr is not None
        hsv = cv2.cvtColor(self.cv_bgr, cv2.COLOR_BGR2HSV).astype(np.int32)
        h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

        blue_candidates = (h >= 85) & (h <= 130) & (s >= 20) & (v >= 120)
        if np.any(blue_candidates):
            med_h = float(np.median(h[blue_candidates]))
            med_s = float(np.median(s[blue_candidates]))
            med_v = float(np.median(v[blue_candidates]))
        else:
            med_h, med_s, med_v = 108.0, 75.0, 215.0

        dh = np.abs(h - med_h)
        ds = np.abs(s - med_s)
        dv = np.abs(v - med_v)

        mask = (
            (dh <= max(10, tolerance // 2))
            & (ds <= tolerance + 25)
            & (dv <= tolerance + 35)
            & (v >= max(110, int(med_v) - tolerance - 30))
        ).astype(np.uint8) * 255

        kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_close, iterations=1)
        return mask

    def detect_sprites(self) -> None:
        if self.cv_bgr is None or self.pil_image is None:
            messagebox.showwarning("Aviso", "Abra uma imagem primeiro.")
            return

        iw, ih = self.pil_image.size
        tol = int(self.color_tol_var.get())
        min_area = int(self.min_area_var.get())
        pad = int(self.pad_var.get())

        mask = self._estimate_light_blue_mask(tol)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        raw_rects: list[list[int]] = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w * h < min_area or w < 8 or h < 8:
                continue
            if w > iw * 0.95 and h > ih * 0.95:
                continue

            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(iw, x + w + pad)
            y2 = min(ih, y + h + pad)
            if x2 > x1 and y2 > y1:
                raw_rects.append([x1, y1, x2, y2])

        if self.split_adj_var.get() and raw_rects:
            raw_rects = self._split_wide_strips(raw_rects, mask)

        self.rects = self._deduplicate_and_sort(raw_rects)
        self.selected_idx = 0 if self.rects else None
        self.refresh_listbox()
        self.redraw()

    def _split_wide_strips(self, rects: list[list[int]], mask: np.ndarray) -> list[list[int]]:
        heights = [r[3] - r[1] for r in rects]
        if not heights:
            return rects
        median_h = float(np.median(heights))
        result: list[list[int]] = []

        for x1, y1, x2, y2 in rects:
            w = x2 - x1
            h = y2 - y1
            if w > median_h * 1.6 and h <= median_h * 1.4:
                sub_mask = (mask[y1:y2, x1:x2] > 0).astype(np.float32)
                col_profile = np.mean(sub_mask, axis=0)
                kernel = np.ones(5, dtype=np.float32) / 5.0
                smooth = np.convolve(col_profile, kernel, mode="same")

                splits = [x1]
                step = max(12, int(median_h * 0.65))
                cur = x1 + step
                while cur < x2 - step * 0.5:
                    win_start = max(0, cur - x1 - 6)
                    win_end = min(w, cur - x1 + 6)
                    if win_end > win_start:
                        local_min_offset = int(np.argmin(smooth[win_start:win_end]))
                        split_x = x1 + win_start + local_min_offset
                        if split_x - splits[-1] >= step * 0.6:
                            splits.append(split_x)
                            cur = split_x + step
                            continue
                    cur += 8
                splits.append(x2)

                for i in range(len(splits) - 1):
                    sx1, sx2 = splits[i], splits[i + 1]
                    if sx2 - sx1 >= 10:
                        result.append([sx1, y1, sx2, y2])
            else:
                result.append([x1, y1, x2, y2])

        return result

    def _deduplicate_and_sort(self, rects: list[list[int]]) -> list[list[int]]:
        if not rects:
            return []

        unique: list[list[int]] = []
        for r in rects:
            is_dup = False
            for u in unique:
                if self._iou(r, u) > 0.85:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(list(r))

        unique.sort(key=lambda r: ((r[1] + r[3]) / 2.0, r[0]))

        heights = [max(1, r[3] - r[1]) for r in unique]
        row_threshold = max(10.0, float(np.median(heights)) * 0.55)

        rows: list[list[list[int]]] = []
        row_centers: list[float] = []

        for r in unique:
            cy = (r[1] + r[3]) / 2.0
            placed = False
            for idx, r_cy in enumerate(row_centers):
                if abs(cy - r_cy) <= row_threshold:
                    rows[idx].append(r)
                    row_centers[idx] = sum((item[1] + item[3]) / 2.0 for item in rows[idx]) / len(rows[idx])
                    placed = True
                    break
            if not placed:
                rows.append([r])
                row_centers.append(cy)

        paired = sorted(zip(row_centers, rows), key=lambda pair: pair[0])
        ordered: list[list[int]] = []
        for _, row_items in paired:
            row_items.sort(key=lambda item: (item[0], item[1]))
            ordered.extend(row_items)

        return ordered

    @staticmethod
    def _iou(a: list[int], b: list[int]) -> float:
        ix1 = max(a[0], b[0])
        iy1 = max(a[1], b[1])
        ix2 = min(a[2], b[2])
        iy2 = min(a[3], b[3])
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = (ix2 - ix1) * (iy2 - iy1)
        area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
        area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
        return inter / float(area_a + area_b - inter)

    def sort_and_refresh(self) -> None:
        self.rects = self._deduplicate_and_sort(self.rects)
        self.selected_idx = 0 if self.rects else None
        self.refresh_listbox()
        self.redraw()

    # -------------------------------------------------------------------------
    # Copiar, Colar, Grade de Frames e Correções Manuais
    # -------------------------------------------------------------------------
    def copy_selected_rect(self) -> None:
        if self.selected_idx is None or not (0 <= self.selected_idx < len(self.rects)):
            messagebox.showwarning("Aviso", "Selecione uma moldura para copiar.")
            return
        self.copied_rect = list(self.rects[self.selected_idx])
        self._paste_offset_step = 0
        w = self.copied_rect[2] - self.copied_rect[0]
        h = self.copied_rect[3] - self.copied_rect[1]
        self.info_var.set(f"Moldura copiada ({w}x{h}px). Pressione Ctrl+V para colar.")

    def paste_copied_rect(self) -> None:
        if self.pil_image is None:
            messagebox.showwarning("Aviso", "Abra uma imagem primeiro.")
            return
        if self.copied_rect is None:
            messagebox.showwarning("Aviso", "Nenhuma moldura copiada. Selecione uma e use Ctrl+C.")
            return

        iw, ih = self.pil_image.size
        x1, y1, x2, y2 = self.copied_rect
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)

        self._paste_offset_step += 1
        offset = 12 * self._paste_offset_step

        nx1 = x1 + offset
        ny1 = y1 + offset
        if nx1 + w > iw or ny1 + h > ih:
            vis_ix, vis_iy = self.canvas_to_img(20, 20)
            nx1 = max(0, min(iw - w, int(round(vis_ix)) + (self._paste_offset_step % 6) * 8))
            ny1 = max(0, min(ih - h, int(round(vis_iy)) + (self._paste_offset_step % 6) * 8))

        new_rect = [nx1, ny1, nx1 + w, ny1 + h]
        self.rects.append(new_rect)
        self.selected_idx = len(self.rects) - 1
        self.refresh_listbox()
        self.redraw()
        self.info_var.set(f"Moldura #{self.selected_idx + 1} colada. Arraste para posicionar livremente.")

    def open_frame_grid_dialog(self) -> None:
        if self.pil_image is None:
            messagebox.showwarning("Aviso", "Abra uma imagem primeiro.")
            return

        if self.selected_idx is not None and 0 <= self.selected_idx < len(self.rects):
            base_rect = list(self.rects[self.selected_idx])
        else:
            base_rect = [0, 0, 32, 32]

        bx1, by1, bx2, by2 = base_rect
        bw = max(1, bx2 - bx1)
        bh = max(1, by2 - by1)

        dialog = tk.Toplevel(self.root)
        dialog.title("Criar Grade de Frames")
        dialog.geometry("360x280")
        dialog.transient(self.root)
        dialog.grab_set()

        pad = ttk.Frame(dialog, padding=12)
        pad.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            pad,
            text=f"Moldura base: ({bx1}, {by1}) • Tamanho: {bw}x{bh}px",
            font=("TkDefaultFont", 9, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        ttk.Label(pad, text="Frames Horizontais (colunas):").grid(row=1, column=0, sticky=tk.W, pady=3)
        cols_var = tk.IntVar(value=4)
        ttk.Spinbox(pad, from_=1, to=200, textvariable=cols_var, width=8).grid(row=1, column=1, sticky=tk.E, pady=3)

        ttk.Label(pad, text="Frames Verticais (linhas):").grid(row=2, column=0, sticky=tk.W, pady=3)
        rows_var = tk.IntVar(value=1)
        ttk.Spinbox(pad, from_=1, to=200, textvariable=rows_var, width=8).grid(row=2, column=1, sticky=tk.E, pady=3)

        ttk.Label(pad, text="Separação Horizontal (px):").grid(row=3, column=0, sticky=tk.W, pady=3)
        gap_x_var = tk.IntVar(value=0)
        ttk.Spinbox(pad, from_=-500, to=2000, textvariable=gap_x_var, width=8).grid(row=3, column=1, sticky=tk.E, pady=3)

        ttk.Label(pad, text="Separação Vertical (px):").grid(row=4, column=0, sticky=tk.W, pady=3)
        gap_y_var = tk.IntVar(value=0)
        ttk.Spinbox(pad, from_=-500, to=2000, textvariable=gap_y_var, width=8).grid(row=4, column=1, sticky=tk.E, pady=3)

        replace_base_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            pad,
            text="Substituir moldura base selecionada",
            variable=replace_base_var,
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(6, 10))

        def apply_grid() -> None:
            cols = max(1, int(cols_var.get()))
            rows = max(1, int(rows_var.get()))
            gap_x = int(gap_x_var.get())
            gap_y = int(gap_y_var.get())
            iw, ih = self.pil_image.size

            if replace_base_var.get() and self.selected_idx is not None and 0 <= self.selected_idx < len(self.rects):
                del self.rects[self.selected_idx]

            created = 0
            for r_idx in range(rows):
                for c_idx in range(cols):
                    fx1 = bx1 + c_idx * (bw + gap_x)
                    fy1 = by1 + r_idx * (bh + gap_y)
                    fx2 = fx1 + bw
                    fy2 = fy1 + bh

                    cx1 = max(0, min(iw, fx1))
                    cy1 = max(0, min(ih, fy1))
                    cx2 = max(0, min(iw, fx2))
                    cy2 = max(0, min(ih, fy2))

                    if (cx2 - cx1) >= 2 and (cy2 - cy1) >= 2:
                        self.rects.append([cx1, cy1, cx2, cy2])
                        created += 1

            self.selected_idx = len(self.rects) - 1 if self.rects else None
            self.refresh_listbox()
            self.redraw()
            self.info_var.set(f"{created} molduras criadas na grade ({cols}x{rows}, sep {gap_x}x{gap_y}px).")
            dialog.destroy()

        btn_frame = ttk.Frame(pad)
        btn_frame.grid(row=6, column=0, columnspan=2, sticky=tk.E, pady=(6, 0))
        ttk.Button(btn_frame, text="Cancelar", command=dialog.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_frame, text="Gerar Molduras", command=apply_grid).pack(side=tk.RIGHT)

    def refresh_listbox(self) -> None:
        self.rect_listbox.delete(0, tk.END)
        for i, (x1, y1, x2, y2) in enumerate(self.rects, start=1):
            w, h = x2 - x1, y2 - y1
            self.rect_listbox.insert(tk.END, f"#{i:03d} | ({x1},{y1}) -> {w}x{h}px")

        self.count_var.set(f"Sprites detectados: {len(self.rects)}")

        if self.selected_idx is not None and 0 <= self.selected_idx < len(self.rects):
            self.rect_listbox.selection_clear(0, tk.END)
            self.rect_listbox.selection_set(self.selected_idx)
            self.rect_listbox.see(self.selected_idx)

    def on_listbox_select(self, _event: tk.Event) -> None:
        sel = self.rect_listbox.curselection()
        if sel:
            self.selected_idx = int(sel[0])
            self.redraw()

    def delete_selected(self) -> None:
        if self.selected_idx is not None and 0 <= self.selected_idx < len(self.rects):
            del self.rects[self.selected_idx]
            if not self.rects:
                self.selected_idx = None
            else:
                self.selected_idx = min(self.selected_idx, len(self.rects) - 1)
            self.refresh_listbox()
            self.redraw()

    def clear_rects(self) -> None:
        self.rects = []
        self.selected_idx = None
        self.refresh_listbox()
        self.redraw()

    def _hit_test_handle(self, idx: int, cx: float, cy: float) -> str | None:
        x1, y1, x2, y2 = self.rects[idx]
        sx1, sy1 = self.img_to_canvas(x1, y1)
        sx2, sy2 = self.img_to_canvas(x2, y2)
        r = 7.0

        corners = {
            "nw": (sx1, sy1),
            "ne": (sx2, sy1),
            "sw": (sx1, sy2),
            "se": (sx2, sy2),
        }
        for name, (hx, hy) in corners.items():
            if abs(cx - hx) <= r and abs(cy - hy) <= r:
                return name

        if abs(cy - sy1) <= 5.0 and sx1 <= cx <= sx2:
            return "n"
        if abs(cy - sy2) <= 5.0 and sx1 <= cx <= sx2:
            return "s"
        if abs(cx - sx1) <= 5.0 and sy1 <= cy <= sy2:
            return "w"
        if abs(cx - sx2) <= 5.0 and sy1 <= cy <= sy2:
            return "e"
        return None

    def on_mouse_down(self, event: tk.Event) -> None:
        if self.pil_image is None:
            return

        ix, iy = self.canvas_to_img(event.x, event.y)
        iw, ih = self.pil_image.size

        if self._eyedropper_active:
            self._eyedropper_active = False
            self.canvas.configure(cursor="")
            px = max(0, min(iw - 1, int(ix)))
            py = max(0, min(ih - 1, int(iy)))
            r, g, b = self.pil_image.getpixel((px, py))
            hex_col = f"#{r:02x}{g:02x}{b:02x}"
            self.chroma_color_var.set(hex_col)
            self.info_var.set(f"Cor capturada: {hex_col} (RGB {r},{g},{b}) em ({px},{py}).")
            return

        self.drag_start_canvas = (event.x, event.y)
        self.drag_start_img = (ix, iy)

        if self.tool_mode_var.get() == "create":
            self.drag_mode = "create"
            self.redraw()
            return

        if self.selected_idx is not None and 0 <= self.selected_idx < len(self.rects):
            handle = self._hit_test_handle(self.selected_idx, event.x, event.y)
            if handle:
                self.drag_mode = "resize"
                self.resize_edge = handle
                self.original_rect_on_drag = list(self.rects[self.selected_idx])
                return

        hit_idx: int | None = None
        for i in range(len(self.rects) - 1, -1, -1):
            x1, y1, x2, y2 = self.rects[i]
            if x1 <= ix <= x2 and y1 <= iy <= y2:
                hit_idx = i
                break

        self.selected_idx = hit_idx
        self.refresh_listbox()

        if hit_idx is not None:
            self.drag_mode = "move"
            self.original_rect_on_drag = list(self.rects[hit_idx])
        else:
            self.drag_mode = "create"

        self.redraw()

    def on_mouse_drag(self, event: tk.Event) -> None:
        if self.pil_image is None or self.drag_mode is None:
            return

        iw, ih = self.pil_image.size
        ix, iy = self.canvas_to_img(event.x, event.y)
        ix = max(0.0, min(float(iw), ix))
        iy = max(0.0, min(float(ih), iy))

        if self.drag_mode == "create":
            self.redraw(
                temp_rect=[
                    int(min(self.drag_start_img[0], ix)),
                    int(min(self.drag_start_img[1], iy)),
                    int(max(self.drag_start_img[0], ix)),
                    int(max(self.drag_start_img[1], iy)),
                ]
            )
            return

        if self.selected_idx is None or self.original_rect_on_drag is None:
            return

        ox1, oy1, ox2, oy2 = self.original_rect_on_drag
        dx = int(round(ix - self.drag_start_img[0]))
        dy = int(round(iy - self.drag_start_img[1]))

        if self.drag_mode == "move":
            w = ox2 - ox1
            h = oy2 - oy1
            nx1 = max(0, min(iw - w, ox1 + dx))
            ny1 = max(0, min(ih - h, oy1 + dy))
            self.rects[self.selected_idx] = [nx1, ny1, nx1 + w, ny1 + h]

        elif self.drag_mode == "resize" and self.resize_edge:
            nx1, ny1, nx2, ny2 = ox1, oy1, ox2, oy2
            if "w" in self.resize_edge:
                nx1 = max(0, min(ox2 - 4, ox1 + dx))
            if "e" in self.resize_edge:
                nx2 = max(ox1 + 4, min(iw, ox2 + dx))
            if "n" in self.resize_edge:
                ny1 = max(0, min(oy2 - 4, oy1 + dy))
            if "s" in self.resize_edge:
                ny2 = max(oy1 + 4, min(ih, oy2 + dy))
            self.rects[self.selected_idx] = [nx1, ny1, nx2, ny2]

        self.refresh_listbox()
        self.redraw()

    def on_mouse_up(self, event: tk.Event) -> None:
        if self.pil_image is None:
            return

        if self.drag_mode == "create":
            iw, ih = self.pil_image.size
            ix, iy = self.canvas_to_img(event.x, event.y)
            x1 = int(max(0, min(iw, min(self.drag_start_img[0], ix))))
            y1 = int(max(0, min(ih, min(self.drag_start_img[1], iy))))
            x2 = int(max(0, min(iw, max(self.drag_start_img[0], ix))))
            y2 = int(max(0, min(ih, max(self.drag_start_img[1], iy))))

            if (x2 - x1) >= 4 and (y2 - y1) >= 4:
                self.rects.append([x1, y1, x2, y2])
                self.selected_idx = len(self.rects) - 1
                self.refresh_listbox()

        self.drag_mode = None
        self.resize_edge = None
        self.original_rect_on_drag = None
        self.redraw()

    # -------------------------------------------------------------------------
    # Renderização no Canvas
    # -------------------------------------------------------------------------
    def redraw(self, temp_rect: list[int] | None = None) -> None:
        self.canvas.delete("all")
        if self.pil_image is None:
            return

        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)

        iw, ih = self.pil_image.size

        vis_x1 = max(0, int(math.floor((0 - self.pan_x) / self.zoom)))
        vis_y1 = max(0, int(math.floor((0 - self.pan_y) / self.zoom)))
        vis_x2 = min(iw, int(math.ceil((cw - self.pan_x) / self.zoom)))
        vis_y2 = min(ih, int(math.ceil((ch - self.pan_y) / self.zoom)))

        if vis_x2 > vis_x1 and vis_y2 > vis_y1:
            crop = self.pil_image.crop((vis_x1, vis_y1, vis_x2, vis_y2))
            out_w = max(1, int(round((vis_x2 - vis_x1) * self.zoom)))
            out_h = max(1, int(round((vis_y2 - vis_y1) * self.zoom)))
            resample = Image.Resampling.NEAREST if self.zoom >= 2.0 else Image.Resampling.BILINEAR
            resized = crop.resize((out_w, out_h), resample)
            self._photo_cache = ImageTk.PhotoImage(resized)
            draw_x, draw_y = self.img_to_canvas(vis_x1, vis_y1)
            self.canvas.create_image(draw_x, draw_y, anchor=tk.NW, image=self._photo_cache)

        for idx, (x1, y1, x2, y2) in enumerate(self.rects):
            sx1, sy1 = self.img_to_canvas(x1, y1)
            sx2, sy2 = self.img_to_canvas(x2, y2)
            if sx2 < 0 or sy2 < 0 or sx1 > cw or sy1 > ch:
                continue

            is_sel = idx == self.selected_idx
            outline = "#00ff88" if is_sel else "#ffdd00"
            width = 2 if is_sel else 1

            self.canvas.create_rectangle(sx1, sy1, sx2, sy2, outline=outline, width=width)
            if self.zoom >= 0.45:
                self.canvas.create_text(
                    sx1 + 2,
                    sy1 + 2,
                    anchor=tk.NW,
                    text=str(idx + 1),
                    fill="#ffffff" if is_sel else "#ffe66d",
                    font=("TkFixedFont", 8, "bold"),
                )

            if is_sel:
                for hx, hy in [(sx1, sy1), (sx2, sy1), (sx1, sy2), (sx2, sy2)]:
                    self.canvas.create_rectangle(hx - 4, hy - 4, hx + 4, hy + 4, fill="#00ff88", outline="#000000")

        if temp_rect is not None:
            tx1, ty1, tx2, ty2 = temp_rect
            sx1, sy1 = self.img_to_canvas(tx1, ty1)
            sx2, sy2 = self.img_to_canvas(tx2, ty2)
            self.canvas.create_rectangle(sx1, sy1, sx2, sy2, outline="#00e5ff", width=2, dash=(4, 2))

    # -------------------------------------------------------------------------
    # Remoção de Fundo (Flood Fill / Chroma Key) e Exportação
    # -------------------------------------------------------------------------
    def _extract_sprite_rgba(self, rect: list[int], bg_mode: str, tol: int) -> Image.Image:
        assert self.pil_image is not None
        x1, y1, x2, y2 = rect
        cropped = self.pil_image.crop((x1, y1, x2, y2)).convert("RGBA")

        if bg_mode == "none":
            return cropped

        arr = np.array(cropped, dtype=np.uint8)
        h, w, _ = arr.shape
        if h == 0 or w == 0:
            return cropped

        rgb = arr[:, :, :3].astype(np.int16)

        if bg_mode == "chroma_key":
            target_rgb = self._parse_hex_color(self.chroma_color_var.get())
            if target_rgb is None:
                border_pixels = np.concatenate(
                    [rgb[0, :, :], rgb[h - 1, :, :], rgb[:, 0, :], rgb[:, w - 1, :]],
                    axis=0,
                )
                key_color = np.median(border_pixels, axis=0)
            else:
                key_color = np.array(target_rgb, dtype=np.int16)

            diff = np.max(np.abs(rgb - key_color), axis=2)
            arr[diff <= tol, 3] = 0
            return Image.fromarray(arr, mode="RGBA")

        border_pixels = np.concatenate(
            [
                rgb[0, :, :],
                rgb[h - 1, :, :],
                rgb[:, 0, :],
                rgb[:, w - 1, :],
            ],
            axis=0,
        )
        bg_color = np.median(border_pixels, axis=0)

        diff = np.max(np.abs(rgb - bg_color), axis=2)
        bg_candidate = diff <= tol

        visited = np.zeros((h, w), dtype=bool)
        queue: deque[tuple[int, int]] = deque()

        for x in range(w):
            for y in (0, h - 1):
                if bg_candidate[y, x] and not visited[y, x]:
                    visited[y, x] = True
                    queue.append((x, y))
        for y in range(h):
            for x in (0, w - 1):
                if bg_candidate[y, x] and not visited[y, x]:
                    visited[y, x] = True
                    queue.append((x, y))

        while queue:
            cx, cy = queue.popleft()
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if 0 <= nx < w and 0 <= ny < h and not visited[ny, nx]:
                    step_diff = int(np.max(np.abs(rgb[ny, nx] - rgb[cy, cx])))
                    if bg_candidate[ny, nx] or step_diff <= max(4, tol // 3):
                        visited[ny, nx] = True
                        queue.append((nx, ny))

        arr[visited, 3] = 0
        return Image.fromarray(arr, mode="RGBA")

    def preview_selected_sprite(self) -> None:
        if self.selected_idx is None or not (0 <= self.selected_idx < len(self.rects)):
            messagebox.showwarning("Aviso", "Selecione um sprite na lista ou na imagem.")
            return

        sprite_img = self._extract_sprite_rgba(
            self.rects[self.selected_idx],
            bg_mode=self.bg_mode_var.get(),
            tol=int(self.bg_tol_var.get()),
        )

        win = tk.Toplevel(self.root)
        win.title(f"Prévia Sprite #{self.selected_idx + 1:03d} ({sprite_img.width}x{sprite_img.height}px)")
        win.geometry("360x360")

        composed = Image.alpha_composite(make_checkerboard(sprite_img.width, sprite_img.height, tile=8), sprite_img)

        scale = min(300 / max(1, composed.width), 300 / max(1, composed.height), 4.0)
        disp = composed.resize(
            (max(1, int(composed.width * scale)), max(1, int(composed.height * scale))),
            Image.Resampling.NEAREST,
        )
        tk_img = ImageTk.PhotoImage(disp)

        lbl = ttk.Label(win, image=tk_img)
        lbl.image = tk_img
        lbl.pack(expand=True, padx=12, pady=12)

    def export_sprites(self) -> None:
        if self.pil_image is None or not self.rects:
            messagebox.showwarning("Aviso", "Não há sprites para exportar.")
            return

        out_dir = filedialog.askdirectory(title="Escolher Pasta de Destino")
        if not out_dir:
            return

        prefix = self.prefix_var.get().strip() or "sprite"
        bg_mode = self.bg_mode_var.get()
        tol = int(self.bg_tol_var.get())

        self.rects = self._deduplicate_and_sort(self.rects)
        self.refresh_listbox()
        self.redraw()

        try:
            for idx, rect in enumerate(self.rects, start=1):
                sprite_img = self._extract_sprite_rgba(rect, bg_mode=bg_mode, tol=tol)
                filename = f"{prefix}_{idx:03d}.png"
                sprite_img.save(os.path.join(out_dir, filename), format="PNG")
        except Exception as exc:
            messagebox.showerror("Erro na exportação", f"Falha ao salvar sprites:\n{exc}")
            return

        messagebox.showinfo("Concluído", f"{len(self.rects)} sprites exportados em:\n{out_dir}")


if __name__ == "__main__":
    root = tk.Tk()
    app = SpriteExtractorApp(root)
    root.mainloop()