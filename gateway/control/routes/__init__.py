from control.routes.observability_routes import (
    handle_observability_delete_route,
    handle_observability_get_route,
    handle_observability_post_route,
)
from control.routes.gateway_routes import (
    handle_gateway_get_route,
    handle_gateway_post_route,
)
from control.routes.capture_routes import (
    handle_capture_get_route,
    handle_capture_post_route,
)
from control.routes.ui_routes import handle_ui_get_route
from control.routes.catalog_routes import handle_catalog_get_route, handle_catalog_post_route
from control.routes.admin_routes import handle_admin_get_route, handle_admin_post_route
from control.routes.operational_routes import (
    handle_operational_delete_route,
    handle_operational_get_route,
    handle_operational_post_route,
)
from control.routes.run_routes import handle_run_get_route, handle_run_post_route
