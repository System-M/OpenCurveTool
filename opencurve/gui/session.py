"""Application state: loaded measurements, settings, active cycle, formulas."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..core import Config, CsvFormatError, analyze, read_measurement
from ..core.datamodel import FileEntry, Formula, FormulaError

MODES = ("single", "range", "all", "mean", "envelope")
MODE_LABEL = {"single": "Single", "range": "Range", "all": "All", "mean": "Mean",
              "envelope": "Envelope"}


class _Worker(QThread):
    """Reads and/or evaluates measurements in the background."""

    progress = Signal(float, str)
    done = Signal(object)          # list of Result
    failed = Signal(str)

    def __init__(self, jobs, cfg, parent=None):
        super().__init__(parent)
        self.jobs = jobs           # list of (path or None, Measurement or None, fmt or None)
        self.cfg = cfg

    def run(self):
        results = []
        try:
            n = len(self.jobs)
            for i, (path, meas, fmt) in enumerate(self.jobs):
                if meas is None:
                    self.progress.emit(i / n, f"Reading {Path(path).name} ...")
                    meas = read_measurement(path, fmt, self.cfg.ca.max_gap_samples)
                results.append(analyze(
                    meas, self.cfg,
                    lambda f, t, i=i: self.progress.emit((i + f) / n, f"{meas.name}: {t}")))
        except CsvFormatError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - shown to the user instead of crashing
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.done.emit(results)


class Session(QObject):
    filesChanged = Signal()            # list of files or the active file changed
    dataChanged = Signal()             # results, formulas or statistics changed
    cycleChanged = Signal(int)
    modeChanged = Signal()
    message = Signal(str)
    busyChanged = Signal(bool)
    progress = Signal(float, str)
    failed = Signal(str)               # loading or calculation failed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = Config()
        self.files: list[FileEntry] = []
        self.active = -1
        self.cycle = 0
        self.mode = "single"
        self.range = (1, 10)           # 1-based, inclusive
        self.formulas: list[Formula] = []
        self.sync = False              # True: no background thread (tests, batch use)
        self._worker = None
        self._queue: list = []         # jobs waiting for the running calculation

    # ------------------------------------------------------------- access
    @property
    def entry(self) -> FileEntry | None:
        return self.files[self.active] if 0 <= self.active < len(self.files) else None

    def entry_for(self, ref) -> FileEntry | None:
        """ref: 'active' or the 1-based number of a loaded file (FILE1, FILE2, ...)."""
        if ref in (None, "active"):
            return self.entry
        try:
            i = int(ref) - 1
        except (TypeError, ValueError):
            return None
        return self.files[i] if 0 <= i < len(self.files) else None

    @property
    def n_cycles(self) -> int:
        return self.entry.n_cycles if self.entry else 0

    @property
    def busy(self) -> bool:
        return self._worker is not None or bool(self._queue)

    def log(self, text: str) -> None:
        self.message.emit(text)

    # ------------------------------------------------------------ loading
    def load_files(self, paths, fmt=None) -> None:
        jobs = [(str(p), None, fmt) for p in paths]
        self._run(jobs, self._loaded)

    def _loaded(self, results) -> None:
        for res in results:
            # a file that is loaded again replaces the old entry
            old = next((i for i, e in enumerate(self.files)
                        if e.meas.path.resolve() == res.meas.path.resolve()), None)
            entry = FileEntry(res, self.formulas)
            if old is None:
                self.files.append(entry)
                self.active = len(self.files) - 1
            else:
                self.files[old] = entry
                self.active = old
            self.log(f"Loaded {res.meas.name}: {res.meas.n} samples, {res.meas.fs:g} Hz, "
                     f"{res.meas.fmt.describe()}")
            for c, cr in res.cyl.items():
                self.log(f"  {cr.name}: {cr.n_cycles} cycles, grid {cr.cmap.res:g} deg")
            for w in res.warnings:
                self.log(f"  - {w}")
        self.cycle = min(self.cycle, max(0, self.n_cycles - 1))
        self.filesChanged.emit()
        self.dataChanged.emit()
        self.cycleChanged.emit(self.cycle)

    def remove_file(self, index: int) -> None:
        if 0 <= index < len(self.files):
            name = self.files[index].name
            del self.files[index]
            self.active = min(self.active, len(self.files) - 1)
            if self.active < 0 and self.files:
                self.active = 0
            self.log(f"Removed {name}")
            self.cycle = min(self.cycle, max(0, self.n_cycles - 1))
            self.filesChanged.emit()
            self.dataChanged.emit()
            self.cycleChanged.emit(self.cycle)

    def set_active(self, index: int) -> None:
        if 0 <= index < len(self.files) and index != self.active:
            self.active = index
            self.cycle = min(self.cycle, max(0, self.n_cycles - 1))
            self.filesChanged.emit()
            self.dataChanged.emit()
            self.cycleChanged.emit(self.cycle)

    # ------------------------------------------------------ recalculation
    def set_config(self, cfg: Config) -> None:
        if cfg.to_dict() == self.cfg.to_dict():
            return
        self.cfg = cfg
        self.recalculate()

    def recalculate(self) -> None:
        if not self.files:
            self.dataChanged.emit()
            return
        jobs = [(None, e.meas, None) for e in self.files]
        self._run(jobs, self._recalculated)

    def _recalculated(self, results) -> None:
        for entry, res in zip(self.files, results):
            for c, cr in res.cyl.items():
                old = entry.result.cyl.get(c)
                if old is not None and old.excluded:
                    cr.excluded = {i for i in old.excluded if i < cr.n_cycles}
                    cr.update_stats()
            entry.result = res
            entry.invalidate()
            for w in res.warnings:
                self.log(f"{res.meas.name}: {w}")
        self.log("Recalculated")
        self.cycle = min(self.cycle, max(0, self.n_cycles - 1))
        self.filesChanged.emit()
        self.dataChanged.emit()
        self.cycleChanged.emit(self.cycle)

    def _run(self, jobs, on_done) -> None:
        if self.sync:
            results = []
            try:
                for path, meas, fmt in jobs:
                    if meas is None:
                        meas = read_measurement(path, fmt, self.cfg.ca.max_gap_samples)
                    results.append(analyze(meas, self.cfg))
            except CsvFormatError as exc:
                self.log(f"Error: {exc}")
                raise
            on_done(results)
            return
        if self._worker is not None:
            self._queue.append((jobs, on_done))
            return
        w = _Worker(jobs, self.cfg.copy(), self)
        self._worker = w
        w.progress.connect(self.progress)
        w.done.connect(lambda r: self._finish(on_done, r))
        w.failed.connect(self._failed)
        self.busyChanged.emit(True)
        w.start()

    def _finish(self, on_done, results) -> None:
        self._cleanup()
        on_done(results)
        self._next()

    def _failed(self, text: str) -> None:
        self._cleanup()
        self.log(f"Error: {text}")
        self.failed.emit(text)
        self._next()

    def _next(self) -> None:
        if self._queue and self._worker is None:
            jobs, on_done = self._queue.pop(0)
            # measurements may have been reloaded meanwhile: recalculation uses the current ones
            if on_done == self._recalculated:
                jobs = [(None, e.meas, None) for e in self.files]
                if not jobs:
                    return self._next()
            self._run(jobs, on_done)

    def _cleanup(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.wait()
            w.deleteLater()
        if not self._queue:
            self.busyChanged.emit(False)

    def wait(self, timeout_ms: int = 60000) -> bool:
        """Process events until all background jobs are done (used by scripts and tests)."""
        from PySide6.QtCore import QCoreApplication, QElapsedTimer
        timer = QElapsedTimer()
        timer.start()
        while (self._worker is not None or self._queue) and timer.elapsed() < timeout_ms:
            QCoreApplication.processEvents()
        return self._worker is None and not self._queue

    # -------------------------------------------------------------- cycle
    def set_cycle(self, index: int) -> None:
        n = self.n_cycles
        index = max(0, min(int(index), n - 1)) if n else 0
        if index != self.cycle:
            self.cycle = index
            self.cycleChanged.emit(index)

    def set_mode(self, mode: str, rng=None) -> None:
        if mode not in MODES:
            return
        if rng is not None:
            self.range = (max(1, int(rng[0])), max(int(rng[0]), int(rng[1])))
        self.mode = mode
        self.modeChanged.emit()

    def toggle_exclude(self, cycle: int | None = None) -> bool:
        """Exclude / include a cycle of the active file from the statistics."""
        e = self.entry
        if e is None:
            return False
        i = self.cycle if cycle is None else cycle
        state = False
        for cr in e.result.cyl.values():
            if i in cr.excluded:
                cr.excluded.discard(i)
            else:
                cr.excluded.add(i)
                state = True
            cr.update_stats()
        e.invalidate()
        self.log(f"Cycle {i + 1} {'excluded from' if state else 'included in'} the statistics")
        self.dataChanged.emit()
        return state

    # ----------------------------------------------------------- formulas
    def set_formulas(self, formulas: list) -> list:
        """Replace all formula channels. Returns a list of error texts."""
        self.formulas[:] = formulas
        errors = []
        for e in self.files:
            e.invalidate()
        if self.entry is not None:
            for f in formulas:
                try:
                    self.entry.check_formula(f)
                except FormulaError as exc:
                    errors.append(f"{f.name}: {exc}")
        self.filesChanged.emit()
        self.dataChanged.emit()
        return errors
