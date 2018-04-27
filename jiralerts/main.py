#!/usr/bin/env python3

import argparse
import flask
import os
import sys
import logging
import json
import time
import datetime

from gourde import Gourde
from jiralerts import issues
from jiralerts import api


LOG_FORMAT = (
    "[%(asctime)s] %(levelname)s %(module)s "
    "[%(filename)s:%(funcName)s:%(lineno)d] (%(thread)d): %(message)s"
)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


class Error(Exception):
    """All local errors."""
    pass


def create_app(args, registry=None):
    gourde = Gourde(__name__, registry=registry)
    app = gourde.app  # This is a flask.Flask() app.
    manager = create_manager(args)

    # Avoid garbage collection:
    gourde.issues_manager = manager

    # TODO: integrate with app.config: http://flask.pocoo.org/docs/0.12/config/
    gourde.args = args
    gourde.is_healthy = manager.ready
    gourde.is_ready = manager.ready

    # Add the clean API.
    api.create_api(gourde.app, manager)

    now = time.time()

    # Add our own index.
    @app.route("/")
    def index():
        return flask.render_template("index.html", manager=manager, starttime=now)

    @app.route("/-/health", endpoint="health2")
    def health():
        # Backward compatibility.
        return gourde.healthy()

    # Old, simple routes.

    @app.route("/issues", methods=["POST"])
    def issues():
        response, code = manager.post_issues(flask.request.get_json())
        return flask.jsonify(response), code

    @app.route("/issues/<project>/<issue_type>", methods=["POST"])
    def file_issue(project, issue_type):
        response, code = manager.post_issues_with_project(
            project, issue_type, flask.request.get_json()
        )
        return flask.jsonify(response), code

    # Setup gourde with the args.
    gourde.setup(args)

    # To make the UI nicer.
    def to_pretty_json(value):
        return json.dumps(value, sort_keys=True, indent=4, separators=(",", ": "))

    gourde.app.jinja_env.filters["pretty_json"] = to_pretty_json

    def to_pretty_timestamp(value):
        return datetime.datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")

    gourde.app.jinja_env.filters["pretty_timestamp"] = to_pretty_timestamp
    return gourde


def create_manager(args):
    """Setup the app itself."""
    resolve_transitions = args.res_transitions.split(",")
    resolved_status = args.res_status.split(",")

    username = os.environ.get("JIRA_USERNAME")
    password = os.environ.get("JIRA_PASSWORD")
    if not username or not password:
        print("JIRA_USERNAME or JIRA_PASSWORD not set")
        sys.exit(2)

    if args.is_async:
        assert args.twisted, "--async only works with --twisted"
        from twisted.internet import reactor
        from twisted.python.threadpool import ThreadPool

        # Create a dedicated thread-pool for processing JIRA requests.
        # this means that we *could* loose work. But since alerts are
        # supposed to be re-sent by alertmanager that's not super bad.
        # Also, the process will quit only once the queue is empty.
        threadpool = ThreadPool(maxthreads=5, name="jira")
        threadpool.start()
        reactor.addSystemEventTrigger("before", "shutdown", threadpool.stop)
    else:
        threadpool = None

    logging.info("Connecting to JIRA..." "")
    manager = issues.Manager(
        basic_auth=(username, password),
        server=args.server,
        resolve_transitions=resolve_transitions,
        resolved_status=resolved_status,
        threadpool=threadpool,
    )
    if args.is_async:
        reactor.callInThread(manager.connect)
    else:
        manager.connect()
    logging.info("Connected to JIRA." "")
    return manager


def parse_args():
    # Setup a custom parser.
    parser = argparse.ArgumentParser(description="jiralert")
    parser = Gourde.get_argparser(parser)
    # Backward compatibility.
    parser.add_argument(
        "--loglevel", default="INFO", help="Log Level, empty string to disable."
    )
    parser.add_argument(
        "--res_transitions",
        default="resolve issue,close issue",
        help="Comma separated list of known transitions used to resolve alerts",
    )
    parser.add_argument(
        "--res_status",
        default="resolved,closed,done,complete",
        help="Comma separated list of known resolved status",
    )
    parser.add_argument(
        "--async",
        default=False,
        dest="is_async",  # async is a reserved keyword.
        action="store_true",
        help="Execute actions asynchronously (useful when jira takes more than 10s).",
    )
    parser.add_argument("server")
    args = parser.parse_args()
    args.log_level = args.loglevel
    return args


def setup_logging(gourde, args):
    # This should probably be moved to Gourde.
    logger = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(gourde.LOG_FORMAT))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.getLevelName(args.log_level))


def main():
    args = parse_args()
    gourde = create_app(args)
    setup_logging(gourde, args)

    gourde.app.jinja_env.auto_reload = True
    gourde.app.config["TEMPLATES_AUTO_RELOAD"] = True
    gourde.run()


if __name__ == "__main__":
    main()
