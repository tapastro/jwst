#! /usr/bin/env python

from ..stpipe import Step
from .. import datamodels
from . import guider_cds
from crds import CrdsLookupError

import logging
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

__all__ = ["GuiderCdsStep"]


class GuiderCdsStep (Step):

    """
    This step calculates the countrate for each pixel for FGS modes.
    """

    class_alias = "guider_cds"

    def process(self, input):
        with datamodels.GuiderRawModel(input) as input_model:

            # Retrieve readnoise reference file
            try:
                rnoise_ref = self.get_reference_file(input_model, 'readnoise')
            except CrdsLookupError:
                # Create mock DataModel to retrieve basic readnoise reference file
                # Note that this is an approximation, as the rmap is not complete for all read patterns
                self.log.warning("Readnoise reference file does not exist for this read pattern - "
                                 "Calculating approximate error array using readnoise reference for "
                                 "FGS60 read pattern.")
                mockmodel = input_model.copy()
                mockmodel.meta.exposure.readpatt = "FGS60"
                rnoise_ref = self.get_reference_file(mockmodel, 'readnoise')

            out_model = guider_cds.guider_cds(input_model, rnoise_ref)

        out_model.meta.cal_step.guider_cds = 'COMPLETE'

        return out_model
