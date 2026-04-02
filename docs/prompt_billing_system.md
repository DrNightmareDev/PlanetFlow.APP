# Claude Prompt

Du arbeitest im Repository `c:\Users\bin04\Projects\PI.4TW` und sollst dich auf den Ordner `planetflow.app` konzentrieren.

## Kontext

`planetflow.app` ist eine FastAPI-/Jinja-Anwendung fuer EVE Online Planetary Industry. Die bestehende Architektur ist account-zentriert:
- Login und Charakter-Verknuepfung laufen ueber EVE SSO
- `Account` und `Character` sind die zentralen Modelle
- Seitenzugriff wird aktuell ueber Middleware + `app/page_access.py` gesteuert
- Der Admin-Bereich erlaubt bereits seitenbezogene Zugriffseinstellungen
- Es gibt aktuell noch kein echtes Billing-/Subscription-System in `planetflow.app`

Im selben Workspace existiert ausserdem `eve_pi_observer`, das bereits eine modernere Billing-/Entitlement-Architektur enthaelt. Nutze dieses Projekt als Referenz fuer Konzepte, aber implementiere die Loesung passend fuer `planetflow.app` und dessen bestehende Struktur. Kopiere nicht blind, sondern integriere sauber.

## Ziel

Ich moechte in `planetflow.app` ein Billing-System mit Grants und Bonus-Codes einfuehren.

## Fachliche Anforderungen

1. Ich moechte eine oder mehrere EVE-Charaktere festlegen koennen, die ISK-Zahlungen empfangen.
2. Eingehende ISK-Zahlungen an diese Charaktere sollen ein Subscription-Modell ausloesen.
3. Eine Subscription soll moeglich sein fuer:
   - einen Main Character / persoenlichen Account
   - eine Corporation
   - eine Alliance
4. Ich moechte die Preise selbst konfigurieren koennen fuer:
   - Single / Individual
   - Corporation mit Preisstufen anhand von Character-Gesamtanzahl
   - Alliance mit Preisstufen anhand von Character-Gesamtanzahl
5. Ich moechte festlegen koennen, welche Seiten kostenlos und welche kostenpflichtig sind.
6. Ich moechte zusaetzlich die Option haben, spaeter kostenpflichtige Funktionen innerhalb einzelner Seiten zu ergaenzen.
7. Es soll Grants geben:
   - manuelle Freischaltungen
   - zeitlich begrenzte Freischaltungen
   - optional seitenbezogen oder global
8. Es soll Bonus-Codes geben:
   - Codes, die Subscription-Zeit vergeben koennen
   - Codes, die einzelne Seiten/Funktionen freischalten koennen
   - optional begrenzt, ablaufbar und auditierbar
9. Das System soll nachvollziehbar und sauber auditierbar sein.
10. Das System soll so gebaut werden, dass spaetere Erweiterungen moeglich bleiben.

## Technische Anforderungen

1. Analysiere zuerst `planetflow.app` gruendlich, insbesondere:
   - `app/models.py`
   - `app/main.py`
   - `app/page_access.py`
   - `app/routers/auth.py`
   - `app/routers/admin.py`
   - relevante Alembic-Migrationen
2. Sieh dir anschliessend die Billing-/Entitlement-Architektur in `eve_pi_observer` an, insbesondere:
   - `app/models.py`
   - `app/services/billing.py`
   - `app/services/entitlements.py`
   - `app/routers/billing.py`
   - `app/tasks.py`
3. Nutze `eve_pi_observer` als Referenz fuer:
   - Wallet Receiver Config
   - Wallet Transaction Import
   - Subscription Periods
   - Free Grants
   - Bonus Codes
   - Entitlement Resolution
4. Das Request-Handling in `planetflow.app` soll DB-first bleiben:
   - keine teuren Live-ESI-Abfragen auf normalen Seitenzugriffen
   - Wallet-Ingest und Matching sollen im Hintergrund laufen
5. Verwende die bestehende Celery-/Task-Struktur von `planetflow.app`, wo sinnvoll.
6. Beruecksichtige EVE-spezifische Einschraenkungen bei Wallet-Journal und Sender-Zuordnung.
7. Das System muss sauber zwischen folgenden Ebenen trennen:
   - Page Access
   - Feature Access innerhalb einer Page
   - Subscription / Entitlement
   - Manual Grants
   - Bonus Codes
8. Fuehre keine uebereilten Codeaenderungen aus, bevor du die bestehende Architektur wirklich verstanden hast.

## Was ich von dir moechte

Erstelle zunaechst einen konkreten, umsetzbaren Implementierungsplan fuer `planetflow.app`.

Der Plan soll enthalten:
1. Zielarchitektur
2. Datenmodell / neue Tabellen / Beziehungen
3. Vorschlag fuer Alembic-Migrationen
4. Entitlement-Modell
5. Wallet-Ingest- und Matching-Logik
6. Preislogik fuer Single / Corp / Alliance inklusive Character-Range-Tiers
7. Admin-UI-Konzept
8. User-UI-/Billing-Seite
9. Middleware-/Access-Control-Anpassungen
10. Konzept fuer spaetere paid-only Funktionen innerhalb einzelner Seiten
11. Sicherheits-, Audit- und Abuse-Ueberlegungen
12. empfohlene Reihenfolge der Umsetzung in Phasen

## Wichtige Vorgaben

- Schlage keine quick-and-dirty Loesung vor.
- Bevorzuge eine saubere, erweiterbare Architektur.
- Passe die Loesung an `planetflow.app` an, nicht an ein generisches SaaS-Beispiel.
- Wenn du Annahmen triffst, kennzeichne sie klar.
- Wenn du Risiken oder offene Architekturentscheidungen siehst, nenne sie explizit.
- Wenn du Verbesserungen gegenueber meiner urspruenglichen Idee empfiehlst, begruende sie knapp.

## Erwartetes Ausgabeformat

- kurze Zusammenfassung des aktuellen Ist-Zustands
- dann der Implementierungsplan in klaren Abschnitten
- dann offene Entscheidungen / Risiken
- dann eine empfohlene erste Umsetzungsphase

Wenn du nach der Analyse der Meinung bist, dass `planetflow.app` zuerst intern auf ein Tenant-/Entitlement-Modell umgestellt werden sollte, bevor Billing ergaenzt wird, dann benenne das klar und begruende es.
