"""
SAP2000 COM Bridge — Manages connection to a local SAP2000 instance via COM.

Supports two modes:
  - Launch a new SAP2000 instance (given a program path)
  - Attach to an already-running instance

All COM interaction is centralized here. Other modules use this bridge
to obtain SapObject and SapModel references.
"""

import comtypes.client
import logging

logger = logging.getLogger(__name__)

# eUnits.kip_in_F — the database units ApplicationStart defaults to when the
# Units argument is omitted.  Passing it explicitly is the only way to reach
# the Visible argument, which is positional after it.
DEFAULT_UNITS = 3

# What GetModelFilename reports when no model has been saved or opened.  A
# freshly launched instance reports "(Untitled)", not an empty string.
_NO_MODEL = {None, "", "(Untitled)"}


class SapBridge:
    """Wrapper around the SAP2000 COM connection."""

    def __init__(self):
        self._sap_object = None
        self._sap_model = None
        self._helper = None
        # True only when this bridge launched the instance itself.  Attaching
        # to an instance a user already had open does not make it ours to close.
        self._owns_instance = False

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def sap_object(self):
        """Return the current cOAPI SapObject, or None if not connected."""
        return self._sap_object

    @property
    def sap_model(self):
        """Return the current cSapModel, or None if not connected."""
        return self._sap_model

    @property
    def is_connected(self) -> bool:
        """True when we hold a live SapObject reference."""
        return self._sap_object is not None

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _create_helper(self):
        """Instantiate the SAP2000 COM helper once."""
        if self._helper is None:
            self._helper = comtypes.client.CreateObject("SAP2000v1.Helper")

    def connect(
        self,
        program_path: str | None = None,
        attach_to_existing: bool = True,
        visible: bool = True,
    ) -> dict:
        """
        Connect to SAP2000.

        Parameters
        ----------
        program_path : str | None
            Full path to SAP2000.exe.  Ignored when *attach_to_existing* is True.
            When None and attach_to_existing is False, the latest installed
            version is launched via ProgID.
        attach_to_existing : bool
            If True, attach to an already-running SAP2000 instance.
        visible : bool
            When launching, start SAP2000 with its window hidden if False.
            When attaching to a running instance, False hides that instance —
            including any model the user already has open on screen.

            Hiding only removes the window; the process still runs, still holds
            a license seat, and still needs a logged-in desktop session.  A
            dialog raised while hidden cannot be dismissed and will block the
            script.

        Returns
        -------
        dict  {connected, version, model_path, units, visible, error}
        """
        if self.is_connected:
            return {
                "connected": True,
                "message": "Already connected to SAP2000.",
                **self._model_summary(),
            }

        self._create_helper()

        try:
            if attach_to_existing:
                self._sap_object = self._helper.GetObject(
                    "CSI.SAP2000.API.SapObject"
                )
                logger.info("Attached to existing SAP2000 instance.")
                self._owns_instance = False
                self._apply_visibility(visible)
            elif program_path:
                self._sap_object = self._helper.CreateObject(program_path)
                self._sap_object.ApplicationStart(DEFAULT_UNITS, visible, "")
                self._owns_instance = True
                logger.info(
                    "Started SAP2000 from %s (visible=%s)", program_path, visible
                )
            else:
                self._sap_object = self._helper.CreateObjectProgID(
                    "CSI.SAP2000.API.SapObject"
                )
                self._sap_object.ApplicationStart(DEFAULT_UNITS, visible, "")
                self._owns_instance = True
                logger.info(
                    "Started latest installed SAP2000 via ProgID (visible=%s).",
                    visible,
                )

            self._sap_model = self._sap_object.SapModel
            summary = self._model_summary()

            if self._owns_instance and summary.get("model_path") not in _NO_MODEL:
                # A freshly launched instance reports no model.  A real model
                # being open means COM handed us an instance that was already
                # running — someone else's session, which we must not close.
                self._owns_instance = False
                summary["warning"] = (
                    "Asked to launch a new instance but received one with a "
                    f"model already open ({summary['model_path']}). Treating it "
                    "as not ours: disconnect will leave it running."
                )
                logger.warning(summary["warning"])

            return {"connected": True, **summary}

        except Exception as exc:
            self._sap_object = None
            self._sap_model = None
            logger.exception("Failed to connect to SAP2000.")
            return {"connected": False, "error": str(exc)}

    def disconnect(
        self,
        save_model: bool = False,
        exit_application: bool | None = None,
    ) -> dict:
        """
        Release the COM connection, and close SAP2000 only when appropriate.

        exit_application
            None (default) — close the application only when this bridge
            launched it.  An instance we merely attached to belongs to whoever
            opened it: dropping the COM references leaves it running with its
            model untouched.
            True — always close it.  Any unsaved work in that instance is lost
            unless save_model is True.
            False — never close it; only release the references.

        Setting references to None is critical — without it SAP2000 may
        hang in Task Manager.
        """
        if not self.is_connected:
            return {"disconnected": True, "message": "Was not connected."}

        should_exit = self._owns_instance if exit_application is None else exit_application
        exited = False
        error = None

        try:
            if should_exit:
                # ApplicationExit fails on a hidden instance — it raises
                # "Invoke cannot be called on a control until the window handle
                # has been created" and leaves the process orphaned.  Show the
                # window again first.
                if self._is_visible() is False:
                    self._sap_object.Unhide()
                self._sap_object.ApplicationExit(save_model)
                exited = True
        except Exception as exc:
            error = str(exc)
            logger.warning("ApplicationExit raised: %s", exc)
        finally:
            self._sap_model = None
            self._sap_object = None
            self._owns_instance = False
            logger.info(
                "Disconnected from SAP2000 (exited=%s, save=%s).",
                exited,
                save_model if exited else False,
            )

        result = {
            "disconnected": True,
            "application_closed": exited,
            "saved": save_model if exited else False,
        }
        if should_exit and not exited:
            result["error"] = (
                f"SAP2000 is still running and no longer connected: {error}"
                if error
                else "SAP2000 is still running and no longer connected."
            )
        return result

    def get_model_info(self) -> dict:
        """
        Return a summary of the current connection and model state.

        Useful for the agent to verify state before/after executing scripts.
        """
        if not self.is_connected:
            return {"connected": False, "error": "Not connected to SAP2000."}

        return {"connected": True, **self._model_summary()}

    def set_visible(self, visible: bool) -> dict:
        """
        Show or hide the SAP2000 window on the connected instance.

        Hide/Unhide return an error when the application is already in the
        requested state, so the current state is checked first.
        """
        if not self.is_connected:
            return {"visible": None, "error": "Not connected to SAP2000."}

        current = self._is_visible()
        if current == visible:
            return {"visible": visible, "changed": False}

        try:
            ret = (
                self._sap_object.Unhide() if visible else self._sap_object.Hide()
            )
        except Exception as exc:
            logger.warning("Hide/Unhide raised: %s", exc)
            return {"visible": current, "changed": False, "error": str(exc)}

        if ret != 0:
            return {
                "visible": self._is_visible(),
                "changed": False,
                "error": f"Hide/Unhide returned {ret}",
            }

        logger.info("Set SAP2000 visibility to %s.", visible)
        return {"visible": visible, "changed": True}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_visible(self) -> bool | None:
        """Current window visibility, or None when it cannot be read."""
        try:
            vis = self._sap_object.Visible
            return bool(vis() if callable(vis) else vis)
        except Exception:
            return None

    def _apply_visibility(self, visible: bool) -> None:
        """
        Hide an attached instance when the caller asked for visible=False.

        Attaching never unhides: a caller leaving *visible* at its default
        should not pop open a window someone else deliberately hid.
        """
        if not visible:
            self.set_visible(False)

    def _model_summary(self) -> dict:
        """Gather basic info from the live SapModel."""
        info: dict = {}
        try:
            info["model_path"] = self._sap_model.GetModelFilename(True)
        except Exception:
            info["model_path"] = None

        try:
            info["version"] = self._sap_object.GetOAPIVersionNumber()
        except Exception:
            info["version"] = None

        try:
            info["units"] = self._sap_model.GetPresentUnits()
        except Exception:
            info["units"] = None

        info["visible"] = self._is_visible()

        try:
            ret_frame = self._sap_model.FrameObj.Count()
            info["num_frames"] = ret_frame if isinstance(ret_frame, int) else ret_frame[0]
        except Exception:
            info["num_frames"] = None

        try:
            ret_point = self._sap_model.PointObj.Count()
            info["num_points"] = ret_point if isinstance(ret_point, int) else ret_point[0]
        except Exception:
            info["num_points"] = None

        try:
            ret_area = self._sap_model.AreaObj.Count()
            info["num_areas"] = ret_area if isinstance(ret_area, int) else ret_area[0]
        except Exception:
            info["num_areas"] = None

        return info


# Module-level singleton so the MCP server and executor share one bridge.
bridge = SapBridge()
