# Strecken-Konfiguration

Eine Datei pro Strecke, Dateiname = normalisierte Strecken-ID der Sim
(z. B. `f1_monza.toml`, `iracing_spa.toml`). Felder:

- `pit_loss_s` – Startwert für den Boxenverlust (wird aus echten Stopps gelernt
  und in der Datenbank gespeichert; die Datei bleibt unverändert)
- `sc_pit_loss_factor` – Anteil des Verlusts unter Safety Car
- `[corners]` – Kurvennamen nach automatisch gelernter Nummer
