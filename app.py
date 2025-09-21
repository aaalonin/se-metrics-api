from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta
from collections import defaultdict, Counter

app = Flask(__name__)

# JIRA API Configuration - Using environment variables for security
import os

JIRA_BASE_URL = os.environ.get('JIRA_BASE_URL', 'https://healthjoy.atlassian.net')
JIRA_EMAIL = os.environ.get('JIRA_EMAIL')
JIRA_API_TOKEN = os.environ.get('JIRA_API_TOKEN')

# Check if credentials are set
if not JIRA_EMAIL or not JIRA_API_TOKEN:
    print("WARNING: JIRA credentials not found in environment variables!")
    print("Set JIRA_EMAIL and JIRA_API_TOKEN in your deployment platform.")

@app.route('/')
def home():
    return jsonify({
        "status": "SE Weekly Metrics API is running!",
        "endpoints": {
            "/metrics": "Get weekly SE metrics",
            "/health": "Health check"
        }
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

@app.route('/test-dates')
def test_dates():
    """Debug endpoint to check date calculations"""
    today = datetime.now()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    # Test with a known week that should have tickets
    test_week_start = datetime(2024, 9, 16)  # Monday Sep 16, 2024
    test_week_end = datetime(2024, 9, 22)    # Sunday Sep 22, 2024

    return jsonify({
        "current_system_date": today.strftime('%Y-%m-%d'),
        "calculated_week_start": week_start.strftime('%Y-%m-%d'),
        "calculated_week_end": week_end.strftime('%Y-%m-%d'),
        "test_week_start": test_week_start.strftime('%Y-%m-%d'),
        "test_week_end": test_week_end.strftime('%Y-%m-%d'),
        "jql_current": f'project = SE AND created >= "{week_start.strftime("%Y-%m-%d")}" AND created <= "{week_end.strftime("%Y-%m-%d")}"',
        "jql_test": f'project = SE AND created >= "{test_week_start.strftime("%Y-%m-%d")}" AND created <= "{test_week_end.strftime("%Y-%m-%d")}"'
    })

@app.route('/metrics')
def get_metrics():
    """Main endpoint that returns SE weekly metrics"""
    try:
        # Initialize metrics storage
        metrics = {
            'new_tickets': [],
            'resolved_tickets': [],
            'current_status_tickets': [],
            'transfer_tickets': [],
            'incident_tickets': [],
            'labels_count': Counter(),
            'se_transfers_resolved': []
        }

        # Calculate LAST week dates (Monday to Sunday) - EXACTLY LIKE se_weekly_metrics_puller.py
        today = datetime.now()
        # Get this Monday, then subtract 7 days to get last Monday
        this_monday = today - timedelta(days=today.weekday())
        week_start = this_monday - timedelta(days=7)
        week_end = week_start + timedelta(days=6)

        # Debug: Print the actual dates being used
        print(f"Today's date: {today.strftime('%Y-%m-%d')}")
        print(f"Week start: {week_start.strftime('%Y-%m-%d')}")
        print(f"Week end: {week_end.strftime('%Y-%m-%d')}")

        print(f"Fetching metrics for: {week_start.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}")

        # Setup authentication
        auth = (JIRA_EMAIL, JIRA_API_TOKEN)
        headers = {'Accept': 'application/json'}

        # FETCH NEW TICKETS THIS WEEK
        new_tickets_jql = f'project = SE AND created >= "{week_start.strftime("%Y-%m-%d")}" AND created <= "{week_end.strftime("%Y-%m-%d")}"'
        new_tickets_response = fetch_jira_data(new_tickets_jql, auth, headers)

        for issue in new_tickets_response:
            fields = issue.get('fields', {})
            labels = fields.get('labels', [])

            # Count labels
            for label in labels:
                if label.lower() != 'support':
                    metrics['labels_count'][label] += 1

            ticket_data = {
                'key': issue['key'],
                'summary': fields.get('summary', ''),
                'status': fields.get('status', {}).get('name', ''),
                'created': fields.get('created', ''),
                'labels': labels,
                'priority': fields.get('priority', {}).get('name', '') if fields.get('priority') else ''
            }

            metrics['new_tickets'].append(ticket_data)

            # Check for incidents
            if (ticket_data['priority'] in ['Highest', 'Critical'] or
                any('incident' in label.lower() for label in labels)):
                metrics['incident_tickets'].append(ticket_data)

        # FETCH RESOLVED TICKETS THIS WEEK - Using multiple methods
        resolved_tickets = []
        all_resolved_keys = set()

        # Method 1: Tickets with resolved field set this week
        resolved_jql1 = f'project = SE AND resolved >= "{week_start.strftime("%Y-%m-%d")}" AND resolved <= "{week_end.strftime("%Y-%m-%d")}"'

        # Method 2: Tickets that changed to Done status this week - MOST ACCURATE
        resolved_jql2 = f'project = SE AND status CHANGED TO "Done" DURING ("{week_start.strftime("%Y-%m-%d")}", "{week_end.strftime("%Y-%m-%d")}")'

        for jql in [resolved_jql1, resolved_jql2]:
            resolved_response = fetch_jira_data(jql, auth, headers, fields="key,summary,status,created,resolved,updated", expand="changelog")

            for issue in resolved_response:
                issue_key = issue['key']
                if issue_key not in all_resolved_keys:
                    all_resolved_keys.add(issue_key)
                    resolved_tickets.append(issue)

        # Process unique resolved tickets
        for issue in resolved_tickets:
            fields = issue.get('fields', {})

            # Calculate resolution time
            created = fields.get('created', '')
            resolved = fields.get('resolved', '')
            resolution_days = 0

            if created and resolved:
                try:
                    created_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    resolved_dt = datetime.fromisoformat(resolved.replace('Z', '+00:00'))
                    resolution_hours = (resolved_dt - created_dt).total_seconds() / 3600
                    resolution_days = resolution_hours / 24
                except:
                    resolution_days = 0
            elif created:
                # If no resolved date, look for resolution in changelog
                resolution_date = find_resolution_date_in_changelog(issue)
                if resolution_date:
                    try:
                        created_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                        resolved_dt = datetime.fromisoformat(resolution_date.replace('Z', '+00:00'))
                        resolution_hours = (resolved_dt - created_dt).total_seconds() / 3600
                        resolution_days = resolution_hours / 24
                    except:
                        resolution_days = 0

            ticket_data = {
                'key': issue['key'],
                'summary': fields.get('summary', ''),
                'status': fields.get('status', {}).get('name', ''),
                'created': created,
                'resolved': resolved,
                'resolution_days': resolution_days
            }

            metrics['resolved_tickets'].append(ticket_data)

        # FETCH ACTIVE TICKETS FOR STATUS ANALYSIS
        active_jql = f'project = SE AND updated >= "{week_start.strftime("%Y-%m-%d")}" AND updated <= "{week_end.strftime("%Y-%m-%d")}" AND status NOT IN ("Done", "Closed", "Resolved")'
        active_response = fetch_jira_data(active_jql, auth, headers)

        for issue in active_response:
            fields = issue.get('fields', {})

            # Calculate days in current status
            updated = fields.get('updated', '')
            days_in_status = 0

            if updated:
                try:
                    updated_dt = datetime.fromisoformat(updated.replace('Z', '+00:00'))
                    days_in_status = (datetime.now() - updated_dt.replace(tzinfo=None)).days
                except:
                    days_in_status = 0

            ticket_data = {
                'key': issue['key'],
                'summary': fields.get('summary', ''),
                'status': fields.get('status', {}).get('name', ''),
                'updated': updated,
                'days_in_status': days_in_status
            }

            metrics['current_status_tickets'].append(ticket_data)

        # FETCH TRANSFER TICKETS THIS WEEK
        teams = ['EIM', 'ENGGMNT', 'AM', 'MRKT']

        for team in teams:
            # Look for tickets created this week that mention SE
            transfer_jql = f'project = {team} AND created >= "{week_start.strftime("%Y-%m-%d")}" AND created <= "{week_end.strftime("%Y-%m-%d")}" AND (text ~ "SE-" OR summary ~ "SE-")'

            transfer_response = fetch_jira_data(transfer_jql, auth, headers, fields="key,summary,status,created,updated,description", expand="changelog", max_results=50)

            for issue in transfer_response:
                fields = issue.get('fields', {})
                summary = fields.get('summary', '')
                description = fields.get('description', '') or ''

                # Check if this is really an SE transfer
                is_se_transfer = False
                original_se_key = "Unknown"

                if 'SE-' in summary or 'SE-' in str(description):
                    is_se_transfer = True
                    # Extract SE key from text
                    import re
                    match = re.search(r'SE-\d+', summary + " " + str(description))
                    if match:
                        original_se_key = match.group(0)

                if is_se_transfer:
                    existing_keys = [t['key'] for t in metrics['transfer_tickets']]
                    if issue['key'] not in existing_keys:
                        ticket_data = {
                            'key': issue['key'],
                            'team': team,
                            'summary': summary,
                            'status': fields.get('status', {}).get('name', ''),
                            'created': fields.get('created', ''),
                            'original_se_key': original_se_key
                        }
                        metrics['transfer_tickets'].append(ticket_data)

        # CALCULATE METRICS
        new_count = len(metrics['new_tickets'])
        resolved_count = len(metrics['resolved_tickets'])
        incident_count = len(metrics['incident_tickets'])
        transfer_count = len(metrics['transfer_tickets'])

        # Average resolution time (include all valid resolutions >= 0)
        resolution_times = [t['resolution_days'] for t in metrics['resolved_tickets'] if t['resolution_days'] >= 0]
        avg_resolution = round(sum(resolution_times) / len(resolution_times), 1) if resolution_times else 0.0

        # Resolution speed buckets
        resolution_buckets = {
            'under_24h': 0,
            '1_3_days': 0,
            '3_7_days': 0,
            'over_7_days': 0
        }

        for days in resolution_times:
            if days < 1.0:
                resolution_buckets['under_24h'] += 1
            elif days <= 3.0:
                resolution_buckets['1_3_days'] += 1
            elif days <= 7.0:
                resolution_buckets['3_7_days'] += 1
            else:
                resolution_buckets['over_7_days'] += 1

        # Status analysis
        status_analysis = defaultdict(lambda: {'count': 0, 'total_days': 0})
        for ticket in metrics['current_status_tickets']:
            status = ticket['status']
            days = ticket['days_in_status']
            status_analysis[status]['count'] += 1
            status_analysis[status]['total_days'] += days

        # Calculate averages for status
        for status, data in status_analysis.items():
            if data['count'] > 0:
                data['avg_days'] = round(data['total_days'] / data['count'], 1)

        # Top 5 labels
        top_labels = metrics['labels_count'].most_common(5)

        # Team transfer breakdown
        team_transfers = defaultdict(int)
        transfer_details = []
        for ticket in metrics['transfer_tickets']:
            team_transfers[ticket['team']] += 1
            if len(transfer_details) < 6:  # Limit for display
                transfer_details.append(ticket)

        # Build response
        return jsonify({
            "success": True,
            "execution_method": "Render_Flask_API",
            "data_source": "Real_JIRA_API",

            # Main metrics
            "newTicketsCount": new_count,
            "resolvedTicketsCount": resolved_count,
            "averageResolutionDays": avg_resolution,
            "incidentsCount": incident_count,
            "transfersCount": transfer_count,

            # Resolution speed buckets
            "speedBuckets": {
                "lessThan24h": resolution_buckets['under_24h'],
                "oneToThreeDays": resolution_buckets['1_3_days'],
                "threeToSevenDays": resolution_buckets['3_7_days'],
                "moreThanSevenDays": resolution_buckets['over_7_days']
            },

            # Week info
            "weekStart": week_start.strftime('%B %d'),
            "weekEnd": week_end.strftime('%B %d, %Y'),
            "weekRange": f"{week_start.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}",

            # Status analysis
            "statusAnalysis": dict(status_analysis),
            "topLabels": [{'label': label, 'count': count} for label, count in top_labels],

            # Transfer information
            "transfers": {
                "total": transfer_count,
                "byTeam": dict(team_transfers),
                "details": transfer_details[:5]  # First 5 transfers
            },

            # Generation info
            "generatedAt": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "execution_method": "Render_Flask_API"
        }), 500

def find_resolution_date_in_changelog(issue):
    """Find the actual resolution date from changelog when resolved field is empty"""
    changelog = issue.get('changelog', {})
    histories = changelog.get('histories', [])

    # Look for status changes to resolved states
    for history in histories:
        items = history.get('items', [])
        for item in items:
            if item.get('field') == 'status':
                to_status = item.get('toString', '')
                if to_status in ['Done', 'Resolved', 'Closed', 'Deployed/Done', 'Complete']:
                    return history.get('created', '')

    return None

def fetch_jira_data(jql, auth, headers, fields="key,summary,status,created,updated,resolved,labels,priority", expand=None, max_results=100):
    """Fetch data from JIRA API with pagination - EXACTLY LIKE se_weekly_metrics_puller.py"""
    all_issues = []
    start_at = 0

    while True:
        params = {
            'jql': jql,
            'fields': fields,
            'maxResults': min(max_results, 100),  # JIRA limit is 100 per request
            'startAt': start_at
        }

        if expand:
            params['expand'] = expand

        response = requests.get(
            f"{JIRA_BASE_URL}/rest/api/3/search/jql",
            auth=auth,
            headers=headers,
            params=params
        )

        if response.status_code != 200:
            print(f"Error fetching JIRA data: {response.status_code}")
            break

        data = response.json()
        issues = data.get('issues', [])
        all_issues.extend(issues)

        total = data.get('total', 0)

        if len(issues) < max_results or start_at + max_results >= total:
            break

        start_at += max_results

    return all_issues

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
