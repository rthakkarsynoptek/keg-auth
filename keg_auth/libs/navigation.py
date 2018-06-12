import inspect
import sys

import flask
import flask_login

from keg_auth.model.utils import has_permissions


def get_defining_class(func):
    if inspect.isclass(func):
        return

    if sys.version_info[0] == 2:
        return getattr(func, 'im_class', None)

    if inspect.ismethod(func):
        for cls in inspect.getmro(func.__self__.__class__):
            if cls.__dict__.get(func.__name__) is func:
                return cls

    if inspect.isfunction(func):
        parse_def = func.__qualname__.split('.<locals>', 1)[0].rsplit('.', 1)
        if len(parse_def) == 1:
            # looks like a method without a class
            return
        return getattr(inspect.getmodule(func), parse_def[0])


class Route(object):
    def __init__(self, route_string, *args, **kwargs):
        self.route_string = route_string
        self.route_args = args
        self.route_kwargs = kwargs
        self.requires_permissions = kwargs.pop('requires_permissions', None)

    @property
    def url(self):
        return flask.url_for(self.route_string, *self.route_args, **self.route_kwargs)

    def __str__(self):
        return self.route_string + str(self.route_args) + str(self.route_kwargs)

    @property
    def is_permitted(self):
        """ Check permitted status of this route for the current user """
        # simplest case: route has requirements directly assigned
        if self.requires_permissions:
            if not flask_login.current_user:
                return False
            return has_permissions(
                self.requires_permissions,
                flask_login.current_user
            )

        # otherwise, we need to find the view for the route. In that case, both the route and its
        #   defining class (if any) may (or may not) have requirements to check.
        # the following checks are ANDed, so return False if anything fails
        view_obj = flask.current_app.view_functions[self.route_string]

        def check_auth(obj):
            if obj is None:
                return True

            if (
                getattr(obj, '__keg_auth_requires_user__', False) and (
                    not flask_login.current_user or
                    not flask_login.current_user.is_authenticated
                )
            ):
                return False

            if (
                getattr(obj, '__keg_auth_requires_permissions__', False) and
                not has_permissions(
                    obj.__keg_auth_requires_permissions__,
                    flask_login.current_user
                )
            ):
                return False

            return True

        if hasattr(view_obj, 'view_class'):
            # class got wrapped with flask's as_view - get the original view to see what
            #   requirements are stored there
            view_obj = view_obj.view_class

        if inspect.isclass(view_obj) and hasattr(view_obj, 'get'):
            # view class has an action method likely to be called via a navigation link
            if sys.version_info[0] != 2:
                view_obj.get.__keg_auth_parent_class__ = view_obj
            view_obj = view_obj.get

        # make sure defining class is assigned (if any). We need to know this in order to
        #   check requirements at the class level
        parent_class = getattr(
            view_obj, 'im_class',
            getattr(view_obj, '__keg_auth_parent_class__', None)
        )
        if not parent_class and not hasattr(view_obj, '__keg_auth_parent_class__'):
            obj = view_obj

            if hasattr(obj, '__keg_auth_original_function__'):
                # the target method has been wrapped by a keg auth decorator, so we need
                #   to inspect the original method to find the parent class (if any)
                obj = obj.__keg_auth_original_function__

            view_obj.__keg_auth_parent_class__ = get_defining_class(obj)
            parent_class = view_obj.__keg_auth_parent_class__

        return check_auth(view_obj) and check_auth(parent_class)


class Node(object):
    class NodeType(object):
        STEM = 0
        LEAF = 1

    def __init__(self, label, *args):
        self.label = label
        self.route = None
        self.sub_nodes = None

        # cache permission-related items
        self._is_permitted = None
        self._permitted_sub_nodes = None

        if len(args) == 0:
            raise Exception('must provide a Route or a list of Nodes')

        if isinstance(args[0], Route):
            self.route = args[0]
            if len(args) > 1:
                args = args[1:]
            else:
                return

        if len(args):
            self.sub_nodes = args

    def clear_authorization(self):
        self._is_permitted = None
        self._permitted_sub_nodes = None
        for sub_node in (self.sub_nodes or []):
            sub_node.clear_authorization()

    @property
    def node_type(self):
        if self.sub_nodes:
            return Node.NodeType.STEM
        return Node.NodeType.LEAF

    @property
    def is_permitted(self):
        if self._is_permitted is None:
            if self.node_type == Node.NodeType.LEAF:
                # checks the route for requirements, or the target view/class
                self._is_permitted = self.route.is_permitted
            else:
                # find a subnode that is permitted
                self._is_permitted = (len(self.permitted_sub_nodes) > 0)

        return self._is_permitted

    @property
    def permitted_sub_nodes(self):
        if self._permitted_sub_nodes is None:
            self._permitted_sub_nodes = [node for node in self.sub_nodes if node.is_permitted]

        return self._permitted_sub_nodes

    def __str__(self):
        if self.node_type == Node.NodeType.LEAF:
            return self.label + str(self.route)
        return self.label + ''.join([str(node) for node in self.sub_nodes])
