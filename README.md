# Time Tracker

A small Tkinter time-tracking app with a GUI-independent domain and JSON storage
layer. The initial interface targets desktop systems; the business logic and
storage are kept separate so the interface or persistence layer can be replaced
later.

## Run

Use Python 3.10 or later with Tkinter installed:

```sh
python -m pip install -r requirements.txt
python -m timetracker
```

The app displays a system tray icon while it is running. Click the icon to
restore and raise the window. Ubuntu requires a desktop environment or extension
that provides a StatusNotifier/AppIndicator host.

The app stores its data under `~/.timetracker`:

- `projects.json` stores client names and projects, each with its assigned client
  and current hourly rate.
- `months/YYYY-MM.json` stores work sessions. In-progress sessions are stored
  under their start month; completed sessions are stored under their end month.

Use **Create invoice** to select a client and date range, then generate an
editable DOCX invoice using the included template. The date range defaults to
the current calendar month. Sessions are included by their end date and only
entries for the selected client appear on the invoice. Invoices are marked
unpaid, due 30 days after the invoice date, and show zero tax and zero payment
received. Invoice numbers are maintained per client code and year in
`invoice_sequences.json`.

Save a project with a client selected or entered. Selecting a saved project fills
its client and hourly rate. Each session records its project, client, hourly-rate
snapshot, start and end times, whole hours and remaining minutes, and turn count.
A session's turn count begins at zero and increases only when **Next turn** is
pressed. The live worked-time display excludes breaks. Use **Start break** and
**End break** to track breaks; the indicator changes color while a break is
active. Ending a session opens a review dialog where start time, stop time, and
total break duration can be adjusted. Worked time and total pay are recalculated
from those values. Pay uses actual whole billable minutes (without rounding time
up) and is rounded to cents; the amount is stored with the completed session.
When a session spans a month boundary, it moves into the month in which it ends.

Starting a session immediately writes an incomplete entry to its monthly file.
Break starts, break ends, and turn increments are saved as they happen. If the
app exits or the system restarts during work, the session remains recoverable:
time continues from the original start time; if a break was active, that break
continues from its recorded start. On the next launch the app restores the most
recent incomplete session and its controls.

## Run tests

```sh
python -m unittest discover -s tests
```
