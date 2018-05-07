"""Jiralert API."""

import flask
import flask_restplus
import flask_cors


# TODO: be stricter in the model.
WEBHOOK_FIELDS = {
    "version": flask_restplus.fields.String(enum=["4", "3"]),
    "groupKey": flask_restplus.fields.String,
    "status": flask_restplus.fields.String(enum=["firing", "resolved"]),
    "receiver": flask_restplus.fields.String,
    "groupLabels": flask_restplus.fields.Raw,
    "commonLabels": flask_restplus.fields.Raw,
    "commonAnnotations": flask_restplus.fields.Raw,
    "alerts": flask_restplus.fields.List(flask_restplus.fields.Raw),
    "externalURL": flask_restplus.fields.String,
}

# TODO: Add a model for the response and use marshalling.


def create_api(app, manager):
    blueprint = flask.Blueprint("api", __name__)
    api = flask_restplus.Api(
        blueprint,
        version="4",
        title="Webhook API",
        description="Alertmanager WebHook API.",
    )

    ns = api.namespace(
        "issues", description="Create or update JIRA issues from alerts."
    )
    issue = api.model("Issue", WEBHOOK_FIELDS)

    app.register_blueprint(blueprint, url_prefix="/api")

    # Allow others to request swagger stuff without restrictions.
    flask_cors.CORS(app, resources={r"/api/swagger.json": {"origins": "*"}})

    @ns.route("")
    class Issues(flask_restplus.Resource):
        """Create or update an issue."""

        @ns.doc("create_todo")
        @ns.expect(issue)
        def post(self):
            """Create an issue."""
            return manager.post_issues(api.payload)

    @ns.route("/<string:project>/<string:issue_type>")
    @ns.param("project", "The JIRA Project")
    @ns.param("issue_type", "JIRA Issue type")
    class ProjectIssues(flask_restplus.Resource):
        """Create or update an issue of a specific type in a specific project."""

        @ns.expect(issue)
        def post(self, project, issue_type):
            """Create an issue with a specific project and issue type."""
            return manager.post_issues_with_project(project, issue_type, api.payload)
