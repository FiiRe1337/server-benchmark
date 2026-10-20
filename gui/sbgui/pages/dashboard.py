from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QProgressBar
import numpy as np, pyqtgraph as pg
from ..widgets import Card, label, chip, table, add_row, plot, Responsive
from ..model import human_bytes, numa_cpus
from .. import theme as T


class Dashboard(QWidget):
    quickstart = Signal(str)

    def __init__(self, info, kern_version="?"):
        super().__init__(); self.setObjectName("page")
        m = info["meta"]; tg = info["targets"]; hv = m["hypervisor"]
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 24); v.setSpacing(16)
        hdr = QHBoxLayout(); tv = QVBoxLayout(); tv.setSpacing(2)
        tv.addWidget(label("Systemübersicht", "h1"))
        tv.addWidget(label(f"{m['host']}  ·  {m['os']['name']} {m['os']['version']} (Build {m['os']['build']})  ·  sbench {kern_version}", "muted"))
        self.dd_label = label(f"Datenverzeichnis (meta.data_dir): {m.get('data_dir') or 'unbekannt (Kern < 1.1)'}", "dim")
        self.dd_label.setTextInteractionFlags(Qt.TextSelectableByMouse); tv.addWidget(self.dd_label)
        hdr.addLayout(tv, 1)
        hdr.addWidget(chip({"host": "Hyper-V Host", "guest": "Hyper-V Gast (VM)", "none": "physisch"}[hv["role"]], "chipAccent"), 0, Qt.AlignTop)
        el = m.get("elevated")  # contract 1.1: sbench (not the GUI) runs elevated?
        self.admin_chip = chip("● Administrator" if el else ("ohne Administratorrechte" if el is False else "Rechte unbekannt"),
                               "chipOk" if el else "chipWarn")
        self.admin_chip.setToolTip("meta.elevated aus sbench info --json. Raw-Ziele (Volume/Disk) fragen vor dem Start nach UAC.")
        hdr.addWidget(self.admin_chip, 0, Qt.AlignTop)
        v.addLayout(hdr)

        g = QGridLayout(); g.setSpacing(14)
        cpu = m["cpu"]
        c1 = Card("CPU", cpu["model"])
        row = QHBoxLayout(); row.addWidget(label(f"{cpu['cores']}C / {cpu['threads']}T", "big")); row.addStretch(1)
        row.addWidget(chip(f"{cpu['sockets']}× Sockel")); row.addWidget(chip(f"{cpu['base_mhz'] / 1000:.1f} GHz"))
        c1.add(row)
        sp = plot(left_width=0); sp.setFixedHeight(40); sp.setMinimumHeight(40); sp.getPlotItem().hideAxis("left"); sp.getPlotItem().hideAxis("bottom")
        sp.getPlotItem().showGrid(False, False)
        rng = np.random.default_rng(3); y = np.clip(14 + np.cumsum(rng.normal(0, 1.4, 90)), 3, 60)
        sp.plot(y, pen=pg.mkPen(T.ACCENT, width=1.6), fillLevel=0, brush=pg.mkBrush(34, 211, 238, 40)); sp.setYRange(0, 70)
        c1.add(sp); c1.add(label(f"Last {y[-1]:.0f} %  ·  60 s, GUI-lokal gemessen", "dim"))
        g.addWidget(c1, 0, 0)

        numa = m.get("numa_nodes") or []
        c2 = Card("Arbeitsspeicher", f"{len(numa)} NUMA-Knoten" if numa else "installiert")
        row = QHBoxLayout(); row.addWidget(label(human_bytes(m["ram_bytes"]), "big")); row.addStretch(1)
        free = m.get("ram_free_bytes")
        if free is not None:
            row.addWidget(chip(f"{human_bytes(free)} frei", "chipOk"))
        c2.add(row)
        if free is not None:
            pb = QProgressBar(); pb.setTextVisible(False); pb.setRange(0, 1000); pb.setFixedHeight(6)
            pb.setValue(int((1 - free / m["ram_bytes"]) * 1000)); c2.add(pb)
        for nd in numa[:4]:
            r = QHBoxLayout(); r.setSpacing(6)
            r.addWidget(label(f"Knoten {nd['id']}", "muted")); r.addStretch(1)
            r.addWidget(label(f"{numa_cpus(nd)} CPUs · {human_bytes(nd['ram_free_bytes'])} / {human_bytes(nd['ram_bytes'])} frei", "dim"))
            c2.add(r)
        if not numa:
            c2.add(label("Tests: Bandbreite (read / write / copy) und Zugriffslatenz", "muted", wrap=True))
        c2.lay.addStretch(1)
        g.addWidget(c2, 0, 1)

        c3 = Card("Hypervisor", hv.get("vendor") or "kein Hypervisor erkannt")
        row = QHBoxLayout()
        row.addWidget(label({"host": "Host", "guest": "Gast-VM", "none": "Bare Metal"}[hv["role"]], "big")); row.addStretch(1)
        row.addWidget(chip("Root-Partition" if hv["role"] == "host" else ("aktiv" if hv["present"] else "nicht vorhanden"),
                           "chipOk" if hv["present"] else "chip"))
        c3.add(row); c3.add(label("VM-Anzahl und Perfcounter über den Test hyperv_info", "muted", wrap=True)); c3.lay.addStretch(1)
        g.addWidget(c3, 0, 2)

        nics = tg["nics"]
        c4 = Card("Netzwerk", f"{len(nics)} Adapter")
        tot = sum((n.get("link_mbps") or 0) for n in nics) / 1000
        row = QHBoxLayout(); row.addWidget(label(f"{tot:g} Gbit/s", "big")); row.addStretch(1)
        row.addWidget(chip(f"{sum(1 for n in nics if n.get('link_mbps'))} Link up", "chipOk")); c4.add(row)
        c4.add(label("Summe der Linkgeschwindigkeiten aller Adapter", "muted", wrap=True)); c4.lay.addStretch(1)
        g.addWidget(c4, 0, 3)
        self.top_cards = [c1, c2, c3, c4]; self.grid = g; self._cols = 4
        for i in range(4):
            g.setColumnStretch(i, 1)
        v.addLayout(g)

        vc = Card("Volumes", "Datei- und Volume-Ziele")
        t = table(["Pfad", "Label", "FS", "Größe", "Frei"], stretch_col=1, mono_cols=(3, 4))
        for d in tg["volumes"]:
            add_row(t, [d["path"].replace("\\\\.\\", ""), d["label"], d["fs"], human_bytes(d["size_bytes"]), human_bytes(d["free_bytes"])])
        vc.add(t)
        pc = Card("Physische Disks", "Raw-Ziele, Zugriff nur mit Administratorrechten")
        t = table(["Pfad", "Modell", "Bus", "Größe", "Sektor l/p"], stretch_col=1, mono_cols=(3,))
        for d in tg["physical_disks"]:
            add_row(t, [d["path"], d["model"], d.get("bus", "–"), human_bytes(d["size_bytes"]),
                        f"{d.get('sector_logical', '?')}/{d.get('sector_physical', '?')}"], {2: T.ACCENT if d.get("bus") == "NVMe" else None, 0: T.MUTED})
        pc.add(t)
        for w in (vc, pc):
            w.setMinimumHeight(170)
        v.addWidget(Responsive([(vc, 2, None), (pc, 3, None)], breakpoint=1000), 1)

        nc = Card("Netzwerkadapter")
        t = table(["Adapter", "IPv4", "Link"], stretch_col=0, mono_cols=(2,))
        for n in nics:
            add_row(t, [n["name"], ", ".join(n.get("ipv4") or []) or "–", f"{n['link_mbps'] / 1000:g} G" if n.get("link_mbps") else "–"])
        nc.add(t); nc.setMinimumHeight(170)
        qs = Card("Schnellstart", "Vordefinierte Jobs, Start mit Standardwerten")
        qg = QGridLayout(); qg.setSpacing(10)
        items = (("Storage 4K random", "QD32 · 4 Worker · 70/30", "storage4k"), ("Storage 1M sequenziell", "QD8 · 2 Worker · Lesen", "storage1m"),
                 ("Four Corners", "5 Tests · ca. 1 min", "quad"), ("Hyper-V Info", "Host/VM, Perfcounter", "hyperv"),
                 ("CPU", "int / float / crypto / mixed", "cpu"), ("RAM", "Bandbreite + Latenz", "memory"),
                 ("Netzwerk", "TCP/UDP gegen net_server", "net"), ("Vollständiger Lauf", "alle Testtypen", "full"))
        for i, (name, sub, key) in enumerate(items):
            b = QPushButton(f"{name}\n{sub}"); b.setObjectName("quick"); b.setMinimumHeight(52)
            b.clicked.connect(lambda _=False, k=key: self.quickstart.emit(k)); qg.addWidget(b, i // 4, i % 4)
        qs.add(qg); qs.lay.addStretch(1)
        v.addWidget(Responsive([(nc, 2, None), (qs, 3, None)], breakpoint=1000), 1)

    def resizeEvent(self, e):
        cols = 4 if e.size().width() >= 1060 else 2
        if cols != self._cols:
            self._cols = cols
            for i, c in enumerate(self.top_cards):
                self.grid.removeWidget(c); self.grid.addWidget(c, i // cols, i % cols)
            for i in range(4):
                self.grid.setColumnStretch(i, 1 if i < cols else 0)
        super().resizeEvent(e)
